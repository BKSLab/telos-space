import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ShiftSuccessorsDialog } from "@/components/calendar/schedule/ShiftSuccessorsDialog";
import { api, endpoints, queryKeys } from "@/lib/api";
import { conflictingSuccessors, isAlreadyLinked } from "@/lib/ganttSchedule";
import { plural } from "@/lib/plural";
import { useToast } from "@/lib/toast";
import type {
    CalendarTask,
    ProjectBaselineResult,
    ScenarioApplyResult,
    ScenarioPreview,
    Task,
    TaskDependency,
    TaskDependencyInput,
} from "@/lib/types";
import { useStoredFlag } from "@/lib/useCalendarView";

interface PendingShift {
    taskKey: string;
    preview: ScenarioPreview;
    onlyThis: () => void;
}

interface UseGanttScheduleOptions {
    projectId: number;
    dependencies: TaskDependency[];
    calendarTasks: CalendarTask[];
    /** Все задачи проекта: последователь может быть за пределами показанного периода. */
    projectTasks: Task[] | undefined;
    /** Создание связи — тем же запросом, что и диалог «Новая зависимость». */
    createDependency: (data: TaskDependencyInput) => void;
}

/**
 * Работа с графиком на шкале: режим «Критический путь», связь протягиванием,
 * вопрос «сдвинуть следующие?» и фиксация плана всего проекта. Страница
 * календаря подключает хук несколькими строками — логика живёт здесь.
 */
export function useGanttSchedule({
    projectId,
    dependencies,
    calendarTasks,
    projectTasks,
    createDependency,
}: UseGanttScheduleOptions) {
    const queryClient = useQueryClient();
    const toast = useToast();
    const [criticalMode, setCriticalMode] = useStoredFlag("telos.calendar.project.critical", false);
    const [pendingShift, setPendingShift] = useState<PendingShift | null>(null);

    const tasksById = useMemo(() => {
        const map = new Map<number, { id: number; start_date: string | null; due_date: string | null }>();
        for (const task of projectTasks ?? []) map.set(task.id, task);
        for (const task of calendarTasks) map.set(task.id, task);
        return map;
    }, [calendarTasks, projectTasks]);

    function refreshProject(taskIds: number[] = []) {
        queryClient.invalidateQueries({ queryKey: ["projects", projectId] });
        queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
        for (const taskId of taskIds) {
            queryClient.invalidateQueries({ queryKey: queryKeys.task(taskId) });
            queryClient.invalidateQueries({ queryKey: queryKeys.taskActivity(taskId) });
        }
    }

    const previewShift = useMutation({
        mutationFn: (change: { task_id: number; start_date: string | null; due_date: string | null }) =>
            api.post<ScenarioPreview>(endpoints.projectCalendarScenarioPreview(projectId), {
                changes: [change],
            }),
    });
    const applyShift = useMutation({
        mutationFn: (preview: ScenarioPreview) =>
            api.post<ScenarioApplyResult>(endpoints.projectCalendarScenarioApply(projectId), {
                changes: preview.changes.map((change) => ({
                    task_id: change.task_id,
                    start_date: change.proposed.start_date,
                    due_date: change.proposed.due_date,
                    expected_updated_at: change.expected_updated_at,
                })),
            }),
        onSuccess: (result) => {
            setPendingShift(null);
            refreshProject(result.task_ids);
            toast.success(`Перенесено задач: ${result.applied_count}.`);
        },
        onError: (error) => toast.error(`Не удалось сдвинуть задачи: ${(error as Error).message}`),
    });
    const fixPlan = useMutation({
        mutationFn: async () => {
            const preview = await api.post<ProjectBaselineResult>(endpoints.projectBaseline(projectId, true));
            if (preview.scheduled_count === 0) {
                toast.error("В проекте нет задач с датами — фиксировать нечего.");
                return null;
            }
            const message =
                preview.changed_count === 0
                    ? `План всех ${preview.scheduled_count} задач с датами уже совпадает с текущими датами. Зафиксировать ещё раз?`
                    : `Зафиксировать план проекта? Это перезапишет план (baseline) у ${preview.changed_count} ${plural(preview.changed_count, "задачи", "задач", "задач")} из ${preview.scheduled_count} с датами.`;
            if (!window.confirm(message)) return null;
            return api.post<ProjectBaselineResult>(endpoints.projectBaseline(projectId));
        },
        onSuccess: (result) => {
            if (!result) return;
            refreshProject(result.task_ids);
            toast.success(
                result.changed_count === 0
                    ? "План проекта уже был зафиксирован."
                    : `План проекта зафиксирован: обновлено ${result.changed_count} ${plural(result.changed_count, "задача", "задачи", "задач")}.`,
            );
        },
        onError: (error) => toast.error(`Не удалось зафиксировать план: ${(error as Error).message}`),
    });

    /** Связь протягиванием: повтор не шлём, цикл сервер отклонит, и тост покажет причину. */
    function linkTasks(predecessorId: number, successorId: number) {
        if (isAlreadyLinked(predecessorId, successorId, dependencies)) {
            toast.error("Эти задачи уже связаны.");
            return;
        }
        createDependency({
            predecessor_task_id: predecessorId,
            successor_task_id: successorId,
            dependency_type: "FINISH_TO_START",
            lag_days: 0,
        });
    }

    /**
     * Перенос полосы с конфликтом у последователей: спрашиваем «сдвинуть и N следующих?».
     * Возвращает true, если перенос перехвачен (вопрос будет задан), иначе вызывающий
     * сохраняет даты как обычно.
     */
    function proposeShift(task: Pick<CalendarTask, "id" | "key">, startDate: string | null, dueDate: string | null, onlyThis: () => void): boolean {
        if (conflictingSuccessors(task.id, dueDate, dependencies, tasksById).length === 0) return false;
        previewShift.mutate(
            { task_id: task.id, start_date: startDate, due_date: dueDate },
            {
                onSuccess: (preview) => {
                    if (preview.consequences_count === 0) onlyThis();
                    else setPendingShift({ taskKey: task.key, preview, onlyThis });
                },
                onError: (error) => {
                    toast.error(`Не удалось рассчитать сдвиг следующих задач: ${(error as Error).message}`);
                    onlyThis();
                },
            },
        );
        return true;
    }

    const dialog = pendingShift ? (
        <ShiftSuccessorsDialog
            taskKey={pendingShift.taskKey}
            preview={pendingShift.preview}
            isApplying={applyShift.isPending}
            onShiftAll={() => applyShift.mutate(pendingShift.preview)}
            onOnlyThis={() => {
                pendingShift.onlyThis();
                setPendingShift(null);
            }}
            onCancel={() => setPendingShift(null)}
        />
    ) : null;

    return {
        criticalMode,
        setCriticalMode,
        linkTasks,
        proposeShift,
        fixProjectPlan: () => fixPlan.mutate(),
        isFixingPlan: fixPlan.isPending,
        dialog,
    };
}
