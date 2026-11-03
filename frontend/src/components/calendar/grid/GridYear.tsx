import { useMemo } from "react";
import { dayOffInfo } from "@/lib/calendar";
import { HEAT_LEGEND, heatLevel, yearDueCounts, yearMonths } from "@/lib/calendarGrid";
import { cn } from "@/lib/cn";
import { plural } from "@/lib/plural";
import type { CalendarGridMilestone, CalendarGridTask } from "@/lib/types";
import { type GridEnv } from "./GridItems";
import { WEEKDAYS, groupMilestones, heatStyle, shortDate } from "./gridHelpers";

const MONTH_NAMES = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"];

/**
 * Год: 12 мини-месяцев, день окрашен по числу сроков (тепловая шкала), вехи — точкой.
 * Клик по дню — месяц на этот день, по названию месяца — месяц.
 */
export function GridYear({
    date,
    tasks,
    milestones,
    env,
}: {
    date: string;
    tasks: CalendarGridTask[];
    milestones: CalendarGridMilestone[];
    env: GridEnv;
}) {
    const year = Number(date.slice(0, 4));
    const months = useMemo(() => yearMonths(year), [year]);
    const counts = useMemo(() => yearDueCounts(tasks, year), [tasks, year]);
    const overdue = useMemo(() => {
        const set = new Set<string>();
        for (const task of tasks) if (task.is_overdue && !task.is_done && task.due_date) set.add(task.due_date);
        return set;
    }, [tasks]);
    const milestonesByDay = useMemo(() => groupMilestones(milestones), [milestones]);
    const total = [...counts.values()].reduce((sum, value) => sum + value, 0);

    return (
        <div className="flex flex-col gap-3 p-3 sm:p-4">
            <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,280px),1fr))] gap-x-6 gap-y-4 lg:grid-cols-[repeat(auto-fill,minmax(236px,1fr))]">
                {months.map(({ month, first, days }) => (
                    <section key={month} className="min-w-0" aria-label={MONTH_NAMES[month]}>
                        <button
                            type="button"
                            onClick={() => env.onOpenPeriod("month", first)}
                            className="mb-1 min-h-11 text-[13px] font-semibold text-primary hover:text-accent lg:min-h-0 lg:text-[12px]"
                            title="Открыть месяц"
                        >
                            {MONTH_NAMES[month]}
                        </button>
                        <div className="grid grid-cols-7 gap-[2px] text-center">
                            {WEEKDAYS.map((name) => (
                                <span key={name} className="text-[9px] text-muted" aria-hidden="true">
                                    {name.slice(0, 1)}
                                </span>
                            ))}
                            {days.map((day) => {
                                if (Number(day.slice(5, 7)) !== month + 1) return <span key={day} aria-hidden="true" />;
                                const count = counts.get(day) ?? 0;
                                const level = heatLevel(count);
                                const dayMilestones = milestonesByDay.get(day) ?? [];
                                const off = dayOffInfo(day, env.holidays);
                                const tooltip = [
                                    shortDate(day),
                                    count > 0 ? `${count} ${plural(count, "срок", "срока", "сроков")}` : "сроков нет",
                                    overdue.has(day) ? "есть просроченные" : null,
                                    ...dayMilestones.map((milestone) => `веха «${milestone.title}»`),
                                    off.title,
                                    "Клик — открыть месяц на этом дне",
                                ]
                                    .filter(Boolean)
                                    .join("\n");
                                return (
                                    <button
                                        key={day}
                                        type="button"
                                        data-heat={level}
                                        data-date={day}
                                        onClick={() => env.onOpenPeriod("month", day)}
                                        title={tooltip}
                                        aria-label={tooltip.split("\n").slice(0, -1).join(", ")}
                                        className={cn(
                                            "relative grid h-10 place-items-center rounded-[4px] font-mono text-[12px] hover:ring-1 hover:ring-accent lg:h-[22px] lg:text-[10px]",
                                            level === 0 && (off.isOff ? "text-disabled" : "text-secondary"),
                                            day === env.today && "ring-2 ring-accent",
                                            overdue.has(day) && "outline outline-1 -outline-offset-1 outline-danger",
                                        )}
                                        style={heatStyle(level)}
                                    >
                                        {Number(day.slice(8, 10))}
                                        {dayMilestones.length > 0 && (
                                            <span aria-hidden="true" className="absolute top-0.5 right-0.5 size-1.5 rounded-full bg-accent ring-1 ring-surface" />
                                        )}
                                    </button>
                                );
                            })}
                        </div>
                    </section>
                ))}
            </div>
            <div className="flex flex-wrap items-center gap-3 border-t border-line pt-2 text-[11px] text-muted">
                <span>
                    {total > 0 ? `${total} ${plural(total, "срок", "срока", "сроков")} за год` : "В этом году сроков нет — задачи без срока в списке «Без срока»."}
                </span>
                <span className="flex items-center gap-1" title="Цвет дня — сколько задач со сроком в этот день">
                    Сроков в день:
                    {HEAT_LEGEND.map((item) => (
                        <span key={item.level} className="grid h-4 min-w-6 place-items-center rounded-[3px] px-1 font-mono text-[9px]" style={heatStyle(item.level)}>
                            {item.label}
                        </span>
                    ))}
                </span>
                <span className="flex items-center gap-1">
                    <span className="size-1.5 rounded-full bg-accent" aria-hidden="true" /> веха
                </span>
                <span className="flex items-center gap-1">
                    <span className="size-3 rounded-[3px] outline outline-1 -outline-offset-1 outline-danger" aria-hidden="true" /> есть просрочка
                </span>
            </div>
        </div>
    );
}
