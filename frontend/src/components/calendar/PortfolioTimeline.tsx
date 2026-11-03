import { useEffect, useMemo, useRef } from "react";
import { dataColor } from "@/lib/dataColor";
import { AlertTriangle, FolderKanban } from "lucide-react";
import { ProjectIcon } from "@/components/projects/ProjectIcon";
import { calendarDaysBetween, type CalendarDayModel, type TimelineRange, type TimelineScale } from "@/lib/calendar";
import { cn } from "@/lib/cn";
import { PROJECT_STATUS_LABELS, type PortfolioCalendarProject } from "@/lib/types";
import { shortDate } from "@/components/calendar/grid/gridHelpers";
import { useMediaQuery } from "@/lib/useCalendarView";

const MONTH_SHORT = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"];

const PORTFOLIO_DAY_WIDTH: Partial<Record<TimelineScale, number>> = { quarter: 12, year: 4 };

function isSunday(date: string): boolean {
    return new Date(`${date}T00:00:00`).getDay() === 0;
}

/**
 * Портфель на шкале: строка на проект — полоса от начала до срока с заливкой по выполнению,
 * красная метка просрочек, вехи ◆. Клик по строке — календарь проекта.
 */
export function PortfolioTimeline({
    projects,
    days,
    range,
    scale,
    onOpenProject,
}: {
    projects: PortfolioCalendarProject[];
    days: CalendarDayModel[];
    range: TimelineRange;
    scale: TimelineScale;
    onOpenProject: (project: PortfolioCalendarProject) => void;
}) {
    const compact = useMediaQuery("(max-width: 1023px)");
    const labelWidth = compact ? 168 : 260;
    const rowHeight = compact ? 72 : 44;
    const dayWidth = PORTFOLIO_DAY_WIDTH[scale] ?? 12;
    const width = days.length * dayWidth;
    const bands = useMemo(() => {
        const list: { key: string; label: string; count: number }[] = [];
        for (const day of days) {
            const key = day.date.slice(0, 7);
            const last = list.at(-1);
            if (last && last.key === key) last.count += 1;
            else list.push({ key, label: `${MONTH_SHORT[Number(key.slice(5, 7)) - 1]} ${key.slice(0, 4)}`, count: 1 });
        }
        return list;
    }, [days]);
    const todayIndex = days.findIndex((day) => day.isToday);
    const scrollRef = useRef<HTMLDivElement>(null);
    useEffect(() => {
        const element = scrollRef.current;
        if (!element) return;
        element.scrollLeft = todayIndex < 0 ? 0 : Math.max(0, todayIndex * dayWidth - (element.clientWidth - labelWidth) / 3);
    }, [dayWidth, labelWidth, range.dateFrom, todayIndex]);

    const x = (date: string) => calendarDaysBetween(range.dateFrom, date) * dayWidth;

    if (projects.length === 0) {
        return (
            <div className="flex flex-col items-center gap-2 py-16 text-center text-[13px] text-muted">
                <FolderKanban size={24} aria-hidden="true" />
                Проектов на шкале нет — вы пока не участвуете ни в одном проекте.
            </div>
        );
    }

    return (
        <section className="instrument-surface flex h-full min-h-0 flex-col overflow-hidden rounded-[var(--radius-panel)]" aria-label="Проекты на шкале">
            <div ref={scrollRef} className="scrollbar-thin min-h-0 flex-1 overflow-auto overscroll-contain" tabIndex={0} data-own-arrows="" aria-label="Шкала проектов, прокручивается по горизонтали">
                <div className="relative" style={{ width: labelWidth + width }}>
                    <div className="sticky top-0 z-30 flex border-b border-line bg-surface-2" style={{ height: 32 }}>
                        <div className="sticky left-0 z-40 flex items-center border-r border-line bg-surface-2 px-3 text-[11px] font-semibold tracking-[0.06em] text-muted uppercase" style={{ width: labelWidth, minWidth: labelWidth }}>
                            Проект · выполнено
                        </div>
                        <div className="relative flex">
                            {bands.map((band) => (
                                <span key={band.key} className="shrink-0 truncate border-r border-line px-1.5 pt-2 text-[10px] font-semibold text-secondary" style={{ width: band.count * dayWidth }}>
                                    {band.label}
                                </span>
                            ))}
                            {todayIndex >= 0 && (
                                <span className="absolute bottom-0 -translate-x-1/2 rounded-t-[3px] bg-accent px-1 text-[9px] leading-[13px] font-semibold text-on-accent" style={{ left: todayIndex * dayWidth + dayWidth / 2 }}>
                                    сегодня
                                </span>
                            )}
                        </div>
                    </div>
                    <div className="relative">
                        <div className="pointer-events-none absolute inset-y-0 flex" style={{ left: labelWidth, width }} aria-hidden="true">
                            {days.map((day) => (
                                <span
                                    key={day.date}
                                    className={cn("shrink-0", isSunday(day.date) && "border-r border-line-subtle", day.isOff && day.holidayTitle !== null && "bg-surface-2/60")}
                                    style={{ width: dayWidth }}
                                />
                            ))}
                        </div>
                        {todayIndex >= 0 && (
                            <div className="pointer-events-none absolute inset-y-0 z-20 w-0.5 bg-accent" style={{ left: labelWidth + todayIndex * dayWidth + dayWidth / 2 }} aria-hidden="true" />
                        )}
                        {projects.map((project) => {
                            const start = project.start_date ?? project.due_date;
                            const end = project.due_date ?? project.start_date;
                            const visible = start !== null && end !== null && end >= range.dateFrom && start <= range.dateTo;
                            const left = visible ? x(start < range.dateFrom ? range.dateFrom : start) : 0;
                            const right = visible ? x(end > range.dateTo ? range.dateTo : end) + dayWidth : 0;
                            const percent = Math.round(project.completion_rate * 100);
                            const dates = start === null ? "сроки не заданы" : `${project.start_date ? shortDate(project.start_date) : "—"} — ${project.due_date ? shortDate(project.due_date) : "без срока"}`;
                            const tooltip = `${project.key} · ${project.name}\n${PROJECT_STATUS_LABELS[project.status]} · ${dates}\nВыполнено ${percent}% (${project.total_tasks} задач)${project.overdue_tasks > 0 ? ` · просрочено ${project.overdue_tasks}` : ""}\nКлик — календарь проекта`;
                            return (
                                <button
                                    key={project.id}
                                    type="button"
                                    onClick={() => onOpenProject(project)}
                                    title={tooltip}
                                    aria-label={`${project.name}: выполнено ${percent}%, ${dates}. Открыть календарь проекта`}
                                    className="group relative flex border-b border-line-subtle text-left hover:bg-hover/60"
                                    style={{ height: rowHeight, width: labelWidth + width }}
                                >
                                    <span className="sticky left-0 z-20 flex items-center gap-2 border-r border-line bg-surface px-3 group-hover:bg-hover" style={{ width: labelWidth, minWidth: labelWidth }}>
                                        <ProjectIcon project={project} size={16} />
                                        <span className="min-w-0 flex-1">
                                            <span className={cn("text-[12px] font-medium text-primary", compact ? "line-clamp-2" : "block truncate")}>{project.name}</span>
                                            <span className="block font-mono text-[10px] text-muted">
                                                {project.key} · {percent}%
                                            </span>
                                            {compact && <span className="block truncate text-[10px] text-muted">{dates}</span>}
                                        </span>
                                        {project.overdue_tasks > 0 && (
                                            <span className="flex shrink-0 items-center gap-0.5 rounded-full bg-danger/10 px-1.5 text-[10px] font-semibold text-danger" title={`Просроченных задач: ${project.overdue_tasks}`}>
                                                <AlertTriangle size={10} aria-hidden="true" />
                                                {project.overdue_tasks}
                                            </span>
                                        )}
                                    </span>
                                    <span className="relative block" style={{ width }}>
                                        {visible && (
                                            <span
                                                className="absolute top-3 h-5 overflow-hidden rounded-[5px] border"
                                                style={{
                                                    left,
                                                    width: Math.max(right - left, dayWidth),
                                                    borderColor: dataColor(project.color),
                                                    background: `color-mix(in srgb, ${dataColor(project.color)} 14%, var(--color-bg-surface))`,
                                                }}
                                            >
                                                <span className="absolute inset-y-0 left-0" style={{ width: `${percent}%`, background: `color-mix(in srgb, ${dataColor(project.color)} 45%, var(--color-bg-surface))` }} />
                                                <span className="relative block truncate px-1.5 text-[10px] leading-[18px] text-primary">{percent}%</span>
                                            </span>
                                        )}
                                        {!visible && (
                                            <span className="absolute top-3.5 left-2 text-[11px] text-muted">{start === null ? "Сроки проекта не заданы" : "Вне показанного периода"}</span>
                                        )}
                                        {project.milestones
                                            .filter((milestone) => milestone.due_date >= range.dateFrom && milestone.due_date <= range.dateTo)
                                            .map((milestone) => (
                                                <span
                                                    key={`${milestone.id}:${milestone.title}`}
                                                    className={cn("absolute top-[9px] z-10 size-2.5 -translate-x-1/2 rotate-45 border", milestone.status === "ACHIEVED" ? "border-success bg-success" : "border-accent bg-surface")}
                                                    style={{ left: x(milestone.due_date) + dayWidth / 2 }}
                                                    title={`Веха «${milestone.title}» · ${shortDate(milestone.due_date)}`}
                                                />
                                            ))}
                                    </span>
                                </button>
                            );
                        })}
                    </div>
                </div>
            </div>
        </section>
    );
}
