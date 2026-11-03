import { useState } from "react";
import { Users } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { calendarDaysBetween, type TimelineRange } from "@/lib/calendar";
import { cn } from "@/lib/cn";
import type { TimelineGroupBy } from "@/lib/timelineGroups";
import {
    WORKLOAD_LEGEND_HINT,
    WORKLOAD_LEVEL_LABELS,
    workloadDayHint,
    type WorkloadLevel,
    type WorkloadPerson,
} from "@/lib/workload";

const LEVEL_CLASS: Record<WorkloadLevel, string> = {
    0: "",
    1: "bg-success/30 text-success",
    2: "bg-warning/55 text-primary",
    3: "bg-danger/65 text-primary font-semibold",
};

const GROUP_OPTIONS: { value: TimelineGroupBy; label: string; title: string }[] = [
    { value: "wbs", label: "ИСР", title: "Разделы структуры работ и их задачи" },
    { value: "stage", label: "Стадиям", title: "Строки шкалы — стадии проекта, как на доске" },
    {
        value: "assignee",
        label: "Исполнителям",
        title: "Строка на человека: его задачи и загрузка по дням, включая другие проекты",
    },
];

/** «Строки по: Стадиям / Исполнителям» — группировка шкалы проекта. */
export function TimelineGroupSwitch({
    value,
    onChange,
}: {
    value: TimelineGroupBy;
    onChange: (value: TimelineGroupBy) => void;
}) {
    return (
        <div
            className="flex items-center gap-1 text-[11px] whitespace-nowrap text-muted"
            title="Как сгруппировать строки шкалы. «Исполнителям» показывает занятость людей по дням"
        >
            <Users size={13} aria-hidden="true" />
            <span className="hidden sm:inline">Строки по</span>
            <div
                className="material-metal flex rounded-[var(--radius-control)] border border-line-subtle p-0.5"
                role="group"
                aria-label="Строки шкалы по"
            >
                {GROUP_OPTIONS.map((item) => (
                    <button
                        key={item.value}
                        type="button"
                        aria-pressed={value === item.value}
                        title={item.title}
                        onClick={() => onChange(item.value)}
                        className={cn(
                            "flex h-11 items-center rounded-[5px] px-2 text-[12px] transition-colors lg:h-6 lg:text-[11px]",
                            value === item.value ? "bg-elevated text-primary shadow-card" : "text-muted hover:text-primary",
                        )}
                    >
                        {item.label}
                    </button>
                ))}
            </div>
        </div>
    );
}

/** Легенда тепловой полосы: ступени и слой «другие проекты». */
export function WorkloadLegend({ truncated }: { truncated?: boolean }) {
    return (
        <div
            className="scrollbar-thin flex min-w-0 flex-nowrap items-center gap-2 overflow-x-auto px-1 pb-1.5 text-[10px] whitespace-nowrap text-muted lg:flex-wrap [&>span]:shrink-0"
            title={WORKLOAD_LEGEND_HINT}
            data-testid="workload-legend"
        >
            <span className="font-semibold tracking-[0.06em] uppercase">Загрузка</span>
            {([1, 2, 3] as const).map((level) => (
                <span key={level} className="flex items-center gap-1">
                    <span aria-hidden="true" className={cn("inline-block h-2.5 w-4 rounded-[2px]", LEVEL_CLASS[level])} />
                    {WORKLOAD_LEVEL_LABELS[level]}
                </span>
            ))}
            <span className="flex items-center gap-1">
                <span aria-hidden="true" className="inline-block h-1 w-4 rounded-full bg-line-strong" />
                другие проекты
            </span>
            <span className="text-muted/80">· нажмите на день — список задач</span>
            {truncated && <span className="text-danger">· задач слишком много, загрузка посчитана не полностью</span>}
        </div>
    );
}

/**
 * Тепловая полоса человека в строке-заголовке: ступень загрузки по дням (этот проект
 * плюс другие) и тонкий серый слой снизу — задачи в других проектах.
 */
export function WorkloadStrip({
    person,
    range,
    dayWidth,
    height,
}: {
    person: WorkloadPerson;
    range: TimelineRange;
    dayWidth: number;
    height: number;
}) {
    const [selectedHint, setSelectedHint] = useState<string | null>(null);
    return (
        <>
        <div className="pointer-events-none absolute inset-0" aria-label={`Загрузка: ${person.name}`} role="list">
            {person.days.map((day) => {
                if (day.date < range.dateFrom || day.date > range.dateTo) return null;
                const left = calendarDaysBetween(range.dateFrom, day.date) * dayWidth;
                const total = day.project_tasks + day.other_tasks;
                const hint = workloadDayHint(day);
                return (
                    <div
                        key={day.date}
                        role="listitem"
                        title={hint}
                        aria-label={hint}
                        data-workload-day={day.date}
                        data-level={day.level}
                        className="pointer-events-auto absolute top-1"
                        style={{ left, width: dayWidth, height: height - 8 }}
                    >
                        <button
                            type="button"
                            aria-label={`Показать загрузку ${person.name} на ${day.date}`}
                            onClick={() => setSelectedHint(hint)}
                            className={cn(
                                "mx-px flex h-[calc(100%-6px)] w-[calc(100%-2px)] items-center justify-center rounded-[3px] font-mono text-[9px] leading-none focus-visible:outline-2 focus-visible:outline-accent",
                                LEVEL_CLASS[day.level],
                            )}
                        >
                            {dayWidth >= 18 && total > 0 ? total : ""}
                        </button>
                        {day.other_tasks > 0 && (
                            <div
                                className="mx-px mt-[2px] h-1 rounded-full bg-line-strong"
                                data-workload-other={day.other_tasks}
                            />
                        )}
                    </div>
                );
            })}
        </div>
        {selectedHint !== null && <Modal title={`Загрузка: ${person.name}`} isOpen onOpenChange={(open) => { if (!open) setSelectedHint(null); }} footer={<Button onClick={() => setSelectedHint(null)}>Закрыть</Button>}>
            <p className="text-[13px] whitespace-pre-line text-secondary">{selectedHint}</p>
        </Modal>}
        </>
    );
}
