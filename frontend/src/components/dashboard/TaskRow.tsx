import type { DashboardTask, TaskPriority } from "@/lib/types";
import type { ReactNode } from "react";
import { formatRelative } from "@/lib/dates";
import { DueDate } from "@/components/ui/DueDate";
import { PriorityBadge, StatusDot } from "@/components/ui/Badge";

export function OverviewTaskRow({ taskKey, title, stage, priority, dotColor, meta, onOpen }: {
    taskKey: string;
    title: string;
    stage?: string | null;
    priority?: TaskPriority;
    dotColor?: string;
    meta?: ReactNode;
    onOpen: () => void;
}) {
    return <button type="button" onClick={onOpen}
        className="flex w-full min-w-0 flex-col gap-2 rounded-[var(--radius-control)] px-2 py-3 text-left transition-colors hover:bg-hover">
        <div className="flex w-full min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-muted">
            {dotColor && <StatusDot color={dotColor} />}
            <span className="font-mono">{taskKey}</span>
            {stage && <span className="max-w-36 truncate">· {stage}</span>}
            <span className="ml-auto">{meta}</span>
        </div>
        <div className="flex w-full min-w-0 items-start gap-3">
            <span className="line-clamp-2 min-w-0 flex-1 text-[14px] leading-relaxed font-medium text-primary">{title}</span>
            {priority && <PriorityBadge priority={priority} className="mt-0.5 shrink-0 text-[12px] tracking-normal normal-case" />}
        </div>
    </button>;
}

interface TaskRowProps {
    task: DashboardTask;
    /** Вместо срока показывает время последнего изменения. */
    showUpdated?: boolean;
    onOpen: (taskId: number) => void;
}

/** Строка задачи в сводке портфеля: одна сущность — один визуальный язык. */
export function TaskRow({ task, showUpdated = false, onOpen }: TaskRowProps) {
    return (
        <OverviewTaskRow
            dotColor={task.project_color}
            taskKey={task.key}
            title={task.title}
            stage={task.stage_name}
            priority={task.priority}
            meta={showUpdated ? formatRelative(task.updated_at) : <DueDate value={task.due_date} className="text-[12px]" />}
            onOpen={() => onOpen(task.id)}
        />
    );
}
