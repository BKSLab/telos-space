import { Inbox } from "lucide-react";
import type { CalendarGridTask, CalendarStage } from "@/lib/types";
import { CalendarTask } from "@/components/calendar/CalendarTask";
import { Button } from "@/components/ui/Button";
import { useMediaQuery } from "@/lib/useCalendarView";

interface UnscheduledListProps {
    tasks: CalendarGridTask[];
    stagesById?: ReadonlyMap<number, CalendarStage>;
    canDrag: boolean;
    hasMore: boolean;
    isLoadingMore: boolean;
    onOpenTask: (taskId: number) => void;
    onScheduleTask?: (task: CalendarGridTask) => void;
    onLoadMore: () => void;
}

/** Задачи без срока: перетащите на день сетки или шкалы — срок назначится. */
export function UnscheduledList({
    tasks,
    stagesById,
    canDrag,
    hasMore,
    isLoadingMore,
    onOpenTask,
    onScheduleTask,
    onLoadMore,
}: UnscheduledListProps) {
    const touchLayout = useMediaQuery("(max-width: 1023px), (pointer: coarse)");
    if (tasks.length === 0) {
        return (
            <div className="flex flex-col items-center gap-2 py-8 text-center text-[12px] text-muted">
                <Inbox size={20} aria-hidden="true" />
                У всех задач есть срок.
            </div>
        );
    }
    return (
        <div className="flex flex-col gap-2">
            <p className="text-[11px] text-muted">
                {canDrag
                    ? touchLayout
                        ? "Нажмите кнопку календаря рядом с задачей, чтобы выбрать начало и срок."
                        : "Перетащите задачу на день сетки или шкалы — у неё появится срок. Кнопка с календарём — выбрать даты вручную."
                    : "Задачи без плановых дат. Назначить срок может участник с правом редактирования."}
            </p>
            <div className="flex flex-col gap-1">
                {tasks.map((task) => (
                    <CalendarTask
                        key={task.id}
                        task={task}
                        stageName={task.source?.name ?? (task.stage_id === null ? undefined : stagesById?.get(task.stage_id)?.name)}
                        draggable={canDrag}
                        onOpen={onOpenTask}
                        onSchedule={canDrag ? onScheduleTask : undefined}
                    />
                ))}
            </div>
            {hasMore && (
                <Button size="sm" variant="ghost" disabled={isLoadingMore} onClick={onLoadMore}>
                    {isLoadingMore ? "Загрузка…" : "Показать ещё"}
                </Button>
            )}
        </div>
    );
}
