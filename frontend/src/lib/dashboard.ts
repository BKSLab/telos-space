import { formatDateOnly, parseDateOnly } from "@/lib/dates";
import type { StageBreakdown, Task } from "@/lib/types";

/** Сроки относятся только к открытым задачам с известной текущей стадией. */
export function projectTaskFocus<T extends Pick<Task, "id" | "stage_id" | "due_date">>(tasks: T[], stages: Pick<StageBreakdown, "stage_id" | "is_done_stage">[], today = formatDateOnly(new Date())) {
    const stageMap = new Map(stages.map(stage => [stage.stage_id, stage]));
    const open = tasks.filter(task => task.stage_id !== null && stageMap.get(task.stage_id)?.is_done_stage === false);
    const scheduled = open.filter(task => task.due_date !== null && parseDateOnly(task.due_date) !== null)
        .sort((first, second) => first.due_date!.localeCompare(second.due_date!) || first.id - second.id);
    const endOfWeek = parseDateOnly(today)!;
    endOfWeek.setDate(endOfWeek.getDate() + 7);
    const soonUntil = formatDateOnly(endOfWeek);

    return {
        scheduled,
        overdue: scheduled.filter(task => task.due_date! < today),
        dueSoon: scheduled.filter(task => task.due_date! >= today && task.due_date! <= soonUntil),
        withoutDeadline: open.filter(task => task.due_date === null),
    };
}
