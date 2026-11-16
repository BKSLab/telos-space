import type { Task } from "@/lib/types";

export interface TaskDateValues {
    startDate: string | null;
    dueDate: string | null;
}

/** Первое назначение отсутствующей даты не требует объяснения. */
export function requiresTaskDateChangeReason(
    task: Pick<Task, "start_date" | "due_date">,
    dates: TaskDateValues,
): boolean {
    return (task.start_date !== null && task.start_date !== dates.startDate)
        || (task.due_date !== null && task.due_date !== dates.dueDate);
}
