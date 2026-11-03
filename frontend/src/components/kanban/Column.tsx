import { useDroppable } from "@dnd-kit/core";
import { useDraggable } from "@dnd-kit/core";
import type { ProjectStage, Task } from "@/lib/types";
import { cn } from "@/lib/cn";
import { StatusDot } from "@/components/ui/Badge";
import { TaskCard } from "@/components/kanban/TaskCard";

function DraggableTask({
    task,
    riskCount,
    isDone,
    isSelected,
    onOpen,
    stages,
    moving,
    onMoveTask,
}: {
    task: Task;
    riskCount: number;
    isDone: boolean;
    isSelected: boolean;
    onOpen: (taskId: number) => void;
    stages: ProjectStage[];
    moving: boolean;
    onMoveTask: (taskId: number, stageId: number) => void;
}) {
    const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: task.id });

    return (
        <TaskCard
            ref={setNodeRef}
            task={task}
            riskCount={riskCount}
            isDone={isDone}
            isSelected={isSelected}
            className={cn(isDragging && "opacity-40")}
            onOpen={onOpen}
            stages={stages}
            moving={moving}
            onMove={stageId => onMoveTask(task.id, stageId)}
            dragHandleProps={{ ...listeners, ...attributes }}
        />
    );
}

interface ColumnProps {
    riskCounts: Record<string, number>;
    stage: ProjectStage;
    tasks: Task[];
    activeStageId: number | null;
    selectedTaskId: number | null;
    onTaskOpen: (taskId: number) => void;
    stages: ProjectStage[];
    moving: boolean;
    onMoveTask: (taskId: number, stageId: number) => void;
}

/**
 * Колонка доски: лёгкая шапка со счётчиком и цветным маркером стадии.
 * Колонка не заливается цветом статуса (раздел 7 дизайн-гайда).
 */
export function Column({ stage, tasks, activeStageId, selectedTaskId, onTaskOpen, riskCounts, stages, moving, onMoveTask }: ColumnProps) {
    const { setNodeRef, isOver } = useDroppable({ id: stage.id });
    const isDropTarget = isOver && activeStageId !== null && activeStageId !== stage.id;

    return (
        <section
            aria-label={`Колонка ${stage.name}`}
            data-stage-id={stage.id}
            className="kanban-column flex shrink-0 flex-col"
        >
            <header className="mb-2.5 flex items-center gap-2 px-2">
                <StatusDot color={stage.color} />
                <h2 className="text-[12px] font-semibold tracking-[0.04em] text-secondary uppercase">
                    {stage.name}
                </h2>
                <span className="rounded-md bg-control px-1.5 py-0.5 font-mono text-[11px] text-muted">{tasks.length}</span>
            </header>

            <div
                ref={setNodeRef}
                className={cn(
                    "scrollbar-thin flex min-h-40 flex-1 flex-col gap-2.5 overflow-y-auto rounded-[var(--radius-panel)] p-2",
                    "border transition-[background-color,border-color] duration-[var(--duration-fast)]",
                    isDropTarget
                        ? "brand-kanban-target"
                        : "border-line-subtle bg-app/20",
                )}
            >
                {tasks.map((task) => (
                    <DraggableTask
                        key={task.id}
                        task={task}
                        riskCount={riskCounts[task.id] ?? 0}
                        isDone={stage.is_done_stage}
                        isSelected={task.id === selectedTaskId}
                        onOpen={onTaskOpen}
                        stages={stages}
                        moving={moving}
                        onMoveTask={onMoveTask}
                    />
                ))}
                {tasks.length === 0 && !isDropTarget && (
                    <p className="px-2 py-6 text-center text-[12px] text-disabled">Пусто</p>
                )}
                {isDropTarget && (
                    <div role="status" className="brand-kanban-insertion">
                        В конец колонки
                    </div>
                )}
            </div>
        </section>
    );
}
