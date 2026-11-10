import { AlertTriangle, CalendarClock, GitCompareArrows, GitMerge, Inbox } from "lucide-react";
import type { CalendarSummary } from "@/lib/types";

export function ProjectCalendarSummary({ summary }: { summary: CalendarSummary }) {
    const signals = [
        {
            label: "Просрочено",
            value: summary.overdue,
            icon: <AlertTriangle size={14} aria-hidden="true" />,
            tone: "text-danger",
        },
        {
            label: "Ближайшие 7 дней",
            value: summary.due_soon,
            icon: <CalendarClock size={14} aria-hidden="true" />,
            tone: "text-warning",
        },
        {
            label: "Без срока",
            value: summary.unscheduled,
            icon: <Inbox size={14} aria-hidden="true" />,
            tone: "text-muted",
        },
        {
            label: "Отклонение от baseline",
            value: summary.drifted,
            icon: <GitCompareArrows size={14} aria-hidden="true" />,
            tone: "text-purple",
        },
        {
            label: "Риски зависимостей",
            value: summary.dependency_risks,
            icon: <GitMerge size={14} aria-hidden="true" />,
            tone: "text-danger",
        },
    ];

    return (
        <aside className="rounded-[var(--radius-card)] bg-surface/55 p-3">
            <div className="mb-2 flex items-center justify-between">
                <h3 className="text-[11px] font-semibold tracking-[0.08em] text-muted uppercase">
                    Обзор проекта
                </h3>
                <span className="size-1.5 rounded-full bg-accent/80" />
            </div>
            <div className="grid sm:grid-cols-3 lg:grid-cols-1">
                {signals.map((signal) => (
                    <div
                        key={signal.label}
                        className="flex items-center gap-2 border-t border-line-subtle px-1 py-2 first:border-t-0"
                    >
                        <span className={signal.tone}>{signal.icon}</span>
                        <span className="min-w-0 flex-1 truncate text-[11px] text-muted">
                            {signal.label}
                        </span>
                        <strong className={`font-mono text-sm ${signal.tone}`}>
                            {signal.value}
                        </strong>
                    </div>
                ))}
            </div>
        </aside>
    );
}
