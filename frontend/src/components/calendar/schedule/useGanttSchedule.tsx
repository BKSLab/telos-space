import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { TaskDatesDialog, type TaskDatesSave } from "@/components/tasks/TaskDatesDialog";
import { ApiError, api, endpoints, queryKeys } from "@/lib/api";
import { isAlreadyLinked } from "@/lib/ganttSchedule";
import { plural } from "@/lib/plural";
import { requiresTaskDateChangeReason, type TaskDateValues } from "@/lib/taskDates";
import { useToast } from "@/lib/toast";
import type {
    CalendarGridTask,
    ProjectBaselineResult,
    ScenarioApplyResult,
    ScenarioPreview,
    TaskDependency,
    TaskDependencyInput,
} from "@/lib/types";
import { useStoredFlag } from "@/lib/useCalendarView";

interface PendingDates {
    task: CalendarGridTask;
    initialDates: TaskDateValues;
    draft: TaskDateValues;
    preview: ScenarioPreview | null;
    includeSuccessors: boolean;
    isPreviewing: boolean;
    error: string | null;
}

interface UseGanttScheduleOptions {
    projectId: number;
    dependencies: TaskDependency[];
    saveDates: (taskId: number, dates: TaskDatesSave, expectedRevision: number) => Promise<unknown>;
    isSavingDates: boolean;
    fixTaskBaseline: (taskId: number) => Promise<unknown>;
    isFixingBaseline: boolean;
    /** Создание связи — тем же запросом, что и диалог «Новая зависимость». */
    createDependency: (data: TaskDependencyInput) => void;
}

/**
 * Работа с графиком на шкале: режим «Критический путь», связь протягиванием,
 * подтверждение дат и сдвига следующих задач в одном окне, фиксация плана. Страница
 * календаря подключает хук несколькими строками — логика живёт здесь.
 */
export function useGanttSchedule({
    projectId,
    dependencies,
    saveDates,
    isSavingDates,
    fixTaskBaseline,
    isFixingBaseline,
    createDependency,
}: UseGanttScheduleOptions) {
    const queryClient = useQueryClient();
    const toast = useToast();
    const [criticalMode, setCriticalMode] = useStoredFlag("telos.calendar.project.critical", false);
    const [pendingDates, setPendingDates] = useState<PendingDates | null>(null);
    const previewRevision = useRef(0);

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
        mutationFn: ({ preview, sourceTask, dates, dateChangeReason }: {
            preview: ScenarioPreview;
            sourceTask: Pick<CalendarGridTask, "id" | "updated_at">;
            dates: TaskDateValues;
            dateChangeReason?: string;
        }) =>
            api.post<ScenarioApplyResult>(endpoints.projectCalendarScenarioApply(projectId), {
                ...(dateChangeReason && { date_change_reason: dateChangeReason }),
                // Основная задача всегда проверяется по снимку открытия окна, даже если
                // свежий preview уже не считает её изменённой. Версии последователей — из расчёта.
                changes: [{
                    task_id: sourceTask.id,
                    start_date: dates.startDate,
                    due_date: dates.dueDate,
                    expected_updated_at: sourceTask.updated_at,
                }, ...preview.changes.filter(change => change.task_id !== sourceTask.id).map((change) => ({
                    task_id: change.task_id,
                    start_date: change.proposed.start_date,
                    due_date: change.proposed.due_date,
                    expected_updated_at: change.expected_updated_at,
                }))],
            }),
        onSuccess: (result) => {
            refreshProject(result.task_ids);
            toast.success(`Перенесено задач: ${result.applied_count}.`);
        },
        onError: (error, { preview, sourceTask }) => {
            if (error instanceof ApiError && error.status === 409) {
                refreshProject([...new Set([sourceTask.id, ...preview.changes.map(change => change.task_id)])]);
            }
            toast.error(`Не удалось сдвинуть задачи: ${(error as Error).message}`);
        },
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

    function closeDates() {
        previewRevision.current += 1;
        setPendingDates(null);
    }

    /** Ответ старого расчёта не может заменить новые даты или вновь открыть закрытое окно. */
    function previewDates(task: CalendarGridTask, dates: TaskDateValues) {
        const revision = ++previewRevision.current;
        const changed = task.start_date !== dates.startDate || task.due_date !== dates.dueDate;
        const reverse = dates.startDate !== null && dates.dueDate !== null && dates.startDate > dates.dueDate;
        const needsPreview = changed && !reverse && dependencies.some((link) => link.predecessor_task_id === task.id);
        setPendingDates((current) => current?.task.id === task.id ? {
            ...current, draft: dates, preview: null, isPreviewing: needsPreview, error: null,
        } : current);
        if (!needsPreview) return;
        previewShift.mutate(
            { task_id: task.id, start_date: dates.startDate, due_date: dates.dueDate },
            {
                onSuccess: (preview) => {
                    if (revision !== previewRevision.current) return;
                    setPendingDates((current) => current ? { ...current, preview, isPreviewing: false } : current);
                },
                onError: (error) => {
                    if (revision !== previewRevision.current) return;
                    setPendingDates((current) => current ? {
                        ...current, isPreviewing: false, includeSuccessors: false,
                        error: `Не удалось рассчитать сдвиг следующих задач: ${(error as Error).message}`,
                    } : current);
                },
            },
        );
    }

    function openDates(task: CalendarGridTask, initialDates: TaskDateValues = { startDate: task.start_date, dueDate: task.due_date }) {
        setPendingDates({ task, initialDates, draft: initialDates, preview: null, includeSuccessors: true, isPreviewing: false, error: null });
        previewDates(task, initialDates);
    }

    /** Перенос и первое назначение дат подтверждаются в одном общем окне. */
    function changeDates(task: CalendarGridTask, dates: TaskDateValues) {
        if (task.start_date === dates.startDate && task.due_date === dates.dueDate) return;
        openDates(task, dates);
    }

    async function saveDialog(dates: TaskDatesSave) {
        if (!pendingDates) return;
        try {
            if (pendingDates.includeSuccessors && pendingDates.preview && pendingDates.preview.consequences_count > 0) {
                await applyShift.mutateAsync({
                    preview: pendingDates.preview, sourceTask: pendingDates.task, dates, dateChangeReason: dates.dateChangeReason,
                });
            } else {
                await saveDates(pendingDates.task.id, dates, pendingDates.task.revision);
            }
            closeDates();
        } catch (error) {
            setPendingDates((current) => current ? { ...current, error: (error as Error).message } : current);
        }
    }

    const isSaving = isSavingDates || isFixingBaseline || applyShift.isPending;
    const cascaded = pendingDates?.preview?.changes.filter((change) => change.source === "CASCADE") ?? [];
    const requireReason = pendingDates ? requiresTaskDateChangeReason(pendingDates.task, pendingDates.draft) || (
        pendingDates.includeSuccessors && cascaded.some((change) => requiresTaskDateChangeReason(change.current, {
            startDate: change.proposed.start_date, dueDate: change.proposed.due_date,
        }))
    ) : false;
    const dialog = pendingDates ? (
        <TaskDatesDialog
            key={pendingDates.task.id}
            task={pendingDates.task}
            initialDates={pendingDates.initialDates}
            isSaving={isSaving}
            error={pendingDates.error}
            requireReason={requireReason}
            saveDisabled={pendingDates.isPreviewing || (pendingDates.includeSuccessors && cascaded.length > 0 && pendingDates.preview !== null && !pendingDates.preview.can_apply)}
            saveLabel={pendingDates.includeSuccessors && cascaded.length > 0 ? "Сохранить и сдвинуть" : undefined}
            onClose={() => { if (!isSaving) closeDates(); }}
            onDatesChange={(dates) => previewDates(pendingDates.task, dates)}
            onSave={(dates) => void saveDialog(dates)}
            onFixBaseline={() => {
                void fixTaskBaseline(pendingDates.task.id).then(closeDates).catch((error: Error) => {
                    setPendingDates((current) => current ? { ...current, error: error.message } : current);
                });
            }}
        >
            {pendingDates.isPreviewing && <p role="status" className="mt-3 text-[12px] text-muted">Расчёт зависимых задач…</p>}
            {cascaded.length > 0 && (
                <div className="mt-3 border-t border-line pt-3">
                    <label className="flex min-h-11 items-center gap-2 text-[13px] text-primary">
                        <input type="checkbox" checked={pendingDates.includeSuccessors} disabled={isSaving}
                            onChange={(event) => setPendingDates((current) => current ? { ...current, includeSuccessors: event.target.checked } : current)} />
                        Сдвинуть зависимые задачи ({cascaded.length})
                    </label>
                    <p className="mb-2 text-[11px] text-muted">Одна причина сохранится у каждой перенесённой задачи.</p>
                    <ul className="flex max-h-48 flex-col gap-1 overflow-auto text-[12px]" aria-label="Задачи, которые сдвинутся">
                        {cascaded.map((change) => (
                            <li key={change.task_id} className="flex justify-between gap-3">
                                <span className="min-w-0 truncate"><span className="font-mono text-[11px] text-accent">{change.task_key}</span> {change.task_title}</span>
                                <span className="shrink-0 font-mono text-[11px] text-muted">{change.current.due_date ?? "—"} → {change.proposed.due_date ?? "—"}</span>
                            </li>
                        ))}
                    </ul>
                </div>
            )}
            {pendingDates.preview?.conflicts.map((conflict) => <p key={`${conflict.code}:${conflict.task_id}`} role="alert" className="mt-2 text-[12px] text-danger">{conflict.message}</p>)}
        </TaskDatesDialog>
    ) : null;

    return {
        criticalMode,
        setCriticalMode,
        linkTasks,
        openDates,
        changeDates,
        fixProjectPlan: () => fixPlan.mutate(),
        isFixingPlan: fixPlan.isPending,
        dialog,
    };
}
