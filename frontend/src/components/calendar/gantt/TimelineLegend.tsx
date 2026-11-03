import { useMemo } from "react";
import type { ColorMode } from "@/lib/calendarGrid";
import { timelineLegend } from "@/lib/ganttView";
import type { CalendarStage, CalendarTask } from "@/lib/types";

/** Компактная легенда под шкалой: цвета полос по «Цвет по», веха, план (baseline) и текущие даты. */
export function TimelineLegend({
    mode,
    tasks,
    stagesById,
}: {
    mode: ColorMode;
    tasks: CalendarTask[];
    stagesById: ReadonlyMap<number, CalendarStage>;
}) {
    const legend = useMemo(() => timelineLegend(mode, tasks, stagesById), [mode, stagesById, tasks]);
    return (
        <div
            aria-label="Легенда шкалы"
            role="note"
            className="scrollbar-thin flex min-w-0 shrink-0 flex-nowrap items-center gap-x-3 gap-y-1 overflow-x-auto px-1 pt-1.5 text-[10px] leading-4 text-secondary lg:flex-wrap [&>span]:shrink-0"
            data-gantt-legend=""
        >
            <span className="font-semibold text-muted" title="Меняется переключателем «Цвет по» в шапке календаря">
                {legend.title}:
            </span>
            {legend.items.map((item) => (
                <span key={item.label} className="flex items-center gap-1">
                    <span aria-hidden="true" className="h-2 w-3.5 rounded-[3px] border" style={{ background: `color-mix(in oklch, ${item.color} 32%, var(--color-bg-surface))`, borderColor: item.color }} />
                    {item.label}
                </span>
            ))}
            <span className="mx-1 h-3 w-px bg-line" aria-hidden="true" />
            <span className="flex items-center gap-1" title="Контрольная точка проекта">
                <span aria-hidden="true" className="size-2 rotate-45 border border-purple bg-surface" />
                Веха
            </span>
            <span className="flex items-center gap-1" title="Полоса — текущие плановые даты задачи">
                <span aria-hidden="true" className="h-2 w-5 rounded-[3px] border border-line-strong bg-surface-2" />
                Текущий план
            </span>
            <span className="flex items-center gap-1" title="Пунктир под полосой — зафиксированный базовый план (baseline), с ним сравнивают отклонение">
                <span aria-hidden="true" className="h-1 w-5 rounded-full border border-dashed border-line-strong" />
                Базовый план
            </span>
            <span className="flex items-center gap-1" title="Вертикальная линия — сегодняшний день">
                <span aria-hidden="true" className="h-3 w-0.5 bg-accent" />
                Сегодня
            </span>
        </div>
    );
}
