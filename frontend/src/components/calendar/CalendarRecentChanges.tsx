import { CalendarClock } from "lucide-react";
import { dayTitle } from "@/lib/calendar";
import type { CalendarDateChange } from "@/lib/types";

function dateLabel(value: string | null): string {
    return value === null ? "без срока" : dayTitle(value);
}

interface CalendarRecentChangesProps {
    changes: CalendarDateChange[];
    onOpenTask: (taskId: number) => void;
}

export function CalendarRecentChanges({ changes, onOpenTask }: CalendarRecentChangesProps) {
    if (changes.length === 0) return null;

    return (
        <section className="rounded-[var(--radius-card)] bg-surface/55 p-3">
            <div className="mb-2 flex items-center gap-2">
                <CalendarClock size={14} className="text-muted" aria-hidden="true" />
                <h3 className="text-[11px] font-semibold tracking-[0.06em] text-muted uppercase">
                    Недавние переносы
                </h3>
            </div>
            <div className="flex flex-col gap-1 lg:max-h-52 lg:overflow-y-auto">
                {changes.map((change) => (
                    <button
                        key={change.id}
                        type="button"
                        onClick={() => onOpenTask(change.task_id)}
                        className="rounded-[var(--radius-control)] px-2 py-1.5 text-left transition-colors hover:bg-hover"
                    >
                        <span className="line-clamp-2 text-[12px] text-primary lg:line-clamp-none lg:truncate lg:text-[11px]">
                            <span className="font-mono text-accent">{change.task_key}</span>{" "}
                            {change.task_title}
                        </span>
                        <span className="block text-[11px] text-muted lg:truncate lg:text-[10px]">
                            {dateLabel(change.from_date)} → {dateLabel(change.to_date)}
                        </span>
                    </button>
                ))}
            </div>
        </section>
    );
}
