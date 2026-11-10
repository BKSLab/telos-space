import "@/components/kanban/kanban.css";
import { useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import {
    DndContext,
    DragOverlay,
    KeyboardSensor,
    MouseSensor,
    TouchSensor,
    useSensor,
    useSensors,
    type DragEndEvent,
    type DragStartEvent,
} from "@dnd-kit/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { ProjectStage, Task } from "@/lib/types";
import { useToast } from "@/lib/toast";
import { useRiskTaskCounts } from "@/lib/useRisks";
import { Column } from "@/components/kanban/Column";
import { TaskCard } from "@/components/kanban/TaskCard";

const POSITION_STEP = 1000;

interface BoardProps {
    projectId: number;
    stages: ProjectStage[];
    tasks: Task[];
    search: string;
    selectedTaskId: number | null;
    onTaskOpen: (taskId: number) => void;
}

interface MoveVariables {
    taskId: number;
    stageId: number;
    position: number;
}

export function Board({
    projectId,
    stages,
    tasks,
    search,
    selectedTaskId,
    onTaskOpen,
}: BoardProps) {
    const queryClient = useQueryClient();
    const riskCounts = useRiskTaskCounts(projectId);
    const toast = useToast();
    const sensors = useSensors(
        useSensor(MouseSensor, { activationConstraint: { distance: 4 } }),
        useSensor(TouchSensor, { activationConstraint: { delay: 280, tolerance: 8 } }),
        useSensor(KeyboardSensor),
    );
    const [activeTask, setActiveTask] = useState<Task | null>(null);
    const [visibleStageId, setVisibleStageId] = useState(stages[0]?.id ?? 0);
    const scrollRef = useRef<HTMLDivElement>(null);
    const visibleStageIndex = Math.max(0, stages.findIndex(stage => stage.id === visibleStageId));

    function showStage(index: number) {
        const stage = stages[index];
        if (!stage) return;
        setVisibleStageId(stage.id);
        const column = scrollRef.current?.querySelector<HTMLElement>(`[data-stage-id="${stage.id}"]`);
        if (column && scrollRef.current) scrollRef.current.scrollTo({ left: column.offsetLeft - 12, behavior: "auto" });
    }

    const tasksByStage = useMemo(() => {
        const map = new Map<number, Task[]>();
        for (const stage of stages) {
            map.set(stage.id, []);
        }
        for (const task of tasks) {
            if (task.stage_id !== null) map.get(task.stage_id)?.push(task);
        }
        for (const list of map.values()) {
            list.sort((first, second) => first.position - second.position);
        }
        return map;
    }, [stages, tasks]);

    const tasksKey = queryKeys.tasks(projectId, search);

    const moveMutation = useMutation({
        mutationFn: ({ taskId, stageId, position }: MoveVariables) =>
            api.patch<Task>(endpoints.taskStagePlacement(taskId), { stage_id: stageId, position }),
        onMutate: async (variables) => {
            await queryClient.cancelQueries({ queryKey: tasksKey });
            const previous = queryClient.getQueryData<Task[]>(tasksKey);
            queryClient.setQueryData<Task[]>(tasksKey, (old) =>
                (old ?? []).map((task) =>
                    task.id === variables.taskId
                        ? { ...task, stage_id: variables.stageId, position: variables.position }
                        : task,
                ),
            );
            return { previous };
        },
        onError: (error, _variables, context) => {
            if (context?.previous) {
                queryClient.setQueryData(tasksKey, context.previous);
            }
            toast.error(`Не удалось переместить задачу: ${(error as Error).message}`);
        },
        onSettled: () => {
            queryClient.invalidateQueries({ queryKey: ["projects", projectId] });
            queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
        },
    });

    function handleDragStart(event: DragStartEvent) {
        setActiveTask(tasks.find((task) => task.id === Number(event.active.id)) ?? null);
    }

    function handleDragEnd(event: DragEndEvent) {
        setActiveTask(null);
        const { active, over } = event;
        if (!over) {
            return;
        }
        const task = tasks.find((item) => item.id === Number(active.id));
        const targetStageId = Number(over.id);
        if (!task || targetStageId === task.stage_id) {
            return;
        }
        moveToStage(task.id, targetStageId);
    }

    function moveToStage(taskId: number, targetStageId: number) {
        const task = tasks.find(item => item.id === taskId);
        if (!task || task.stage_id === targetStageId || !stages.some(stage => stage.id === targetStageId) || moveMutation.isPending) return;
        const destination = tasksByStage.get(targetStageId) ?? [];
        const maxPosition = destination.reduce((max, item) => Math.max(max, item.position), 0);
        moveMutation.mutate({
            taskId: task.id,
            stageId: targetStageId,
            position: maxPosition + POSITION_STEP,
        });
    }

    return (
        <DndContext
            sensors={sensors}
            onDragStart={handleDragStart}
            onDragEnd={handleDragEnd}
            onDragCancel={() => setActiveTask(null)}
        >
            <div className="kanban-workspace">
                <nav className="kanban-stage-nav" aria-label="Навигация по стадиям">
                    <button type="button" aria-label="Предыдущая стадия" disabled={visibleStageIndex === 0} onClick={() => showStage(visibleStageIndex - 1)}><ChevronLeft size={18} aria-hidden="true" /></button>
                    <select aria-label="Показать стадию" value={stages[visibleStageIndex]?.id ?? ""} onChange={event => showStage(stages.findIndex(stage => stage.id === Number(event.target.value)))}>
                        {stages.map(stage => <option key={stage.id} value={stage.id}>{stage.name} · {tasksByStage.get(stage.id)?.length ?? 0}</option>)}
                    </select>
                    <button type="button" aria-label="Следующая стадия" disabled={visibleStageIndex >= stages.length - 1} onClick={() => showStage(visibleStageIndex + 1)}><ChevronRight size={18} aria-hidden="true" /></button>
                </nav>
            <div ref={scrollRef} className="kanban-scroll scrollbar-thin" onScroll={event => {
                const container = event.currentTarget;
                const columns = [...container.querySelectorAll<HTMLElement>("[data-stage-id]")];
                const nearest = columns.reduce<HTMLElement | null>((best, column) => !best || Math.abs(column.offsetLeft - container.scrollLeft - 12) < Math.abs(best.offsetLeft - container.scrollLeft - 12) ? column : best, null);
                if (nearest) setVisibleStageId(Number(nearest.dataset.stageId));
            }}>
                <div className="kanban-columns">
                    {stages.map((stage) => (
                        <Column
                            key={stage.id}
                            stage={stage}
                            riskCounts={riskCounts.data ?? {}}
                            tasks={tasksByStage.get(stage.id) ?? []}
                            activeStageId={activeTask?.stage_id ?? null}
                            selectedTaskId={selectedTaskId}
                            onTaskOpen={onTaskOpen}
                            stages={stages}
                            moving={moveMutation.isPending}
                            onMoveTask={moveToStage}
                        />
                    ))}
                </div>
            </div>
            </div>

            <DragOverlay>
                {activeTask && (
                    <div className="w-[300px]">
                        <TaskCard task={activeTask} riskCount={riskCounts.data?.[activeTask.id] ?? 0} isDragging onOpen={() => undefined} />
                    </div>
                )}
            </DragOverlay>
        </DndContext>
    );
}
