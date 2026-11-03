import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { addDateDays, dayOffInfo } from "@/lib/calendar";
import {
    coversDate,
    gridRange,
    isMultiDay,
    isoWeekNumber,
    layoutLanes,
    monthCellLayout,
    taskColor,
    visibleBarRows,
} from "@/lib/calendarGrid";
import { cn } from "@/lib/cn";
import type { CalendarGridMilestone, CalendarGridTask } from "@/lib/types";
import { AnchoredPanel, GridMilestoneChip, GridTaskBar, GridTaskCard, type GridEnv } from "./GridItems";
import { DayItemsList } from "./GridDay";
import { ITEM_HEIGHT, WEEKDAYS, byImportance, groupMilestones, groupSingles, isEmptySpotClick, shortDate, useDayDrop } from "./gridHelpers";

const DAY_HEADER = 22;

type MoreState = { date: string; anchor: HTMLElement } | null;

export function GridMonth({
    date,
    tasks,
    milestones,
    env,
    isPhone,
}: {
    date: string;
    tasks: CalendarGridTask[];
    milestones: CalendarGridMilestone[];
    env: GridEnv;
    isPhone: boolean;
}) {
    const range = gridRange("month", date);
    const month = date.slice(0, 7);
    const weeks = useMemo(
        () => Array.from({ length: 6 }, (_, index) => addDateDays(range.dateFrom, index * 7)),
        [range.dateFrom],
    );
    const singles = useMemo(() => groupSingles(tasks), [tasks]);
    const milestonesByDay = useMemo(() => groupMilestones(milestones), [milestones]);
    const multi = useMemo(() => tasks.filter(isMultiDay), [tasks]);
    const bodyRef = useRef<HTMLDivElement>(null);
    const [maxRows, setMaxRows] = useState(4);
    const [more, setMore] = useState<MoreState>(null);
    const [phoneDay, setPhoneDay] = useState<string | null>(null);

    useEffect(() => {
        const element = bodyRef.current;
        if (!element || isPhone) return;
        const measure = () => {
            const rowHeight = element.clientHeight / 6;
            setMaxRows(Math.max(2, Math.min(8, Math.floor((rowHeight - DAY_HEADER - 2) / ITEM_HEIGHT))));
        };
        measure();
        const observer = new ResizeObserver(measure);
        observer.observe(element);
        return () => observer.disconnect();
    }, [isPhone]);

    const dayItems = (day: string) => ({
        milestones: milestonesByDay.get(day) ?? [],
        tasks: tasks.filter((task) => coversDate(task, day)).sort(byImportance),
    });

    if (isPhone) {
        const selected = phoneDay !== null && phoneDay >= range.dateFrom && phoneDay <= range.dateTo ? phoneDay : date;
        return (
            <div className="flex flex-col gap-3 p-2">
                <div className="grid grid-cols-7 text-center text-[10px] font-semibold text-muted uppercase">
                    {WEEKDAYS.map((name) => (
                        <span key={name} className="py-1">{name}</span>
                    ))}
                </div>
                <div className="grid grid-cols-7 gap-px overflow-hidden rounded-[var(--radius-card)] border border-line bg-line" role="grid" aria-label="Месяц">
                    {weeks.flatMap((weekFrom) =>
                        Array.from({ length: 7 }, (_, column) => {
                            const day = addDateDays(weekFrom, column);
                            const items = dayItems(day);
                            const off = dayOffInfo(day, env.holidays);
                            const count = items.tasks.length + items.milestones.length;
                            return (
                                <button
                                    key={day}
                                    type="button"
                                    data-date={day}
                                    onClick={() => setPhoneDay(day)}
                                    aria-pressed={selected === day}
                                    aria-label={`${shortDate(day)}: задач ${items.tasks.length}${items.milestones.length ? `, вех ${items.milestones.length}` : ""}`}
                                    title={off.title ?? undefined}
                                    className={cn(
                                        "flex h-12 flex-col items-center gap-1 pt-1 text-[12px]",
                                        off.isOff ? "bg-surface-2" : "bg-surface",
                                        day.slice(0, 7) !== month && "text-disabled",
                                        selected === day && "ring-2 ring-inset ring-accent",
                                    )}
                                >
                                    <span className={cn("grid size-5 place-items-center rounded-full", day === env.today && "bg-accent font-semibold text-on-accent")}>
                                        {Number(day.slice(8, 10))}
                                    </span>
                                    <span className="flex h-1.5 items-center gap-0.5" aria-hidden="true">
                                        {items.milestones.slice(0, 1).map((milestone) => (
                                            <span key={`m${milestone.id}${milestone.title}`} className="size-1.5 rotate-45 bg-accent" />
                                        ))}
                                        {items.tasks.slice(0, 3 - Math.min(items.milestones.length, 1)).map((task) => (
                                            <span key={task.id} className="size-1.5 rounded-full" style={{ background: task.is_overdue && !task.is_done ? "var(--color-danger)" : taskColor(task, env.colorMode, env.stagesById) }} />
                                        ))}
                                        {count > 3 && <span className="text-[8px] leading-none text-muted">+</span>}
                                    </span>
                                </button>
                            );
                        }),
                    )}
                </div>
                <div className="flex items-center justify-between gap-2">
                    <h3 className="text-[13px] font-semibold text-primary">{shortDate(selected)}</h3>
                    {env.onCreate && <Button size="sm" icon={<Plus size={16} aria-hidden="true" />} onClick={() => env.onCreate?.(selected)}>Новая задача</Button>}
                </div>
                <DayItemsList date={selected} tasks={tasks} milestones={milestones} env={env} />
            </div>
        );
    }

    return (
        <div className="flex h-full min-h-[560px] flex-col">
            <div className="grid shrink-0 grid-cols-[28px_repeat(7,minmax(0,1fr))] border-b border-line text-[10px] font-semibold tracking-[0.06em] text-muted uppercase">
                <span />
                {WEEKDAYS.map((name, index) => (
                    <span key={name} className={cn("px-2 py-1.5", index >= 5 && "text-disabled")}>{name}</span>
                ))}
            </div>
            <div ref={bodyRef} className="grid min-h-0 flex-1 grid-rows-6" role="grid" aria-label="Месяц">
                {weeks.map((weekFrom) => {
                    const { segments, laneCount } = layoutLanes(multi, weekFrom);
                    const barRows = visibleBarRows(laneCount, maxRows);
                    return (
                        <div key={weekFrom} className="relative grid min-h-0 grid-cols-[28px_repeat(7,minmax(0,1fr))] border-b border-line last:border-b-0" role="row">
                            <span
                                className="flex justify-center pt-1.5 font-mono text-[10px] text-muted"
                                title={`Неделя ${isoWeekNumber(weekFrom)} — открыть неделю`}
                            >
                                <button type="button" className="hover:text-accent" onClick={() => env.onOpenPeriod("week", weekFrom)} aria-label={`Неделя ${isoWeekNumber(weekFrom)}`}>
                                    {isoWeekNumber(weekFrom)}
                                </button>
                            </span>
                            {Array.from({ length: 7 }, (_, column) => {
                                const day = addDateDays(weekFrom, column);
                                const daySingles = singles.get(day) ?? [];
                                const dayMilestones = milestonesByDay.get(day) ?? [];
                                const barLanes = segments
                                    .filter((segment) => segment.startCol <= column && column < segment.startCol + segment.span)
                                    .map((segment) => segment.lane);
                                const layout = monthCellLayout({
                                    barLanes,
                                    barRows,
                                    singles: daySingles.length + dayMilestones.length,
                                    maxRows,
                                });
                                const shownMilestones = dayMilestones.slice(0, layout.shownSingles);
                                const shownTasks = daySingles.slice(0, Math.max(0, layout.shownSingles - shownMilestones.length));
                                return (
                                    <MonthDayCell
                                        key={day}
                                        day={day}
                                        inMonth={day.slice(0, 7) === month}
                                        env={env}
                                        barRows={barRows}
                                        hidden={layout.hidden}
                                        onMore={(anchor) => setMore({ date: day, anchor })}
                                    >
                                        {shownMilestones.map((milestone) => (
                                            <GridMilestoneChip key={`${milestone.id}:${milestone.title}`} milestone={milestone} showProject={env.showSource} />
                                        ))}
                                        {shownTasks.map((task) => (
                                            <GridTaskCard key={task.id} task={task} date={day} env={env} part={`m:${day}`} />
                                        ))}
                                    </MonthDayCell>
                                );
                            })}
                            <div className="pointer-events-none absolute inset-y-0 right-0 left-[28px]">
                                {segments
                                    .filter((segment) => segment.lane < barRows)
                                    .map((segment) => (
                                        <GridTaskBar
                                            key={segment.item.id}
                                            task={segment.item}
                                            rowFrom={weekFrom}
                                            startCol={segment.startCol}
                                            span={segment.span}
                                            top={DAY_HEADER + segment.lane * ITEM_HEIGHT}
                                            clippedStart={segment.clippedStart}
                                            clippedEnd={segment.clippedEnd}
                                            env={env}
                                        />
                                    ))}
                            </div>
                        </div>
                    );
                })}
            </div>
            {more && (
                <AnchoredPanel anchor={more.anchor} label={`Все задачи: ${shortDate(more.date)}`} onClose={() => setMore(null)}>
                    <DayItemsList date={more.date} tasks={dayItems(more.date).tasks} milestones={dayItems(more.date).milestones} env={env} compact />
                </AnchoredPanel>
            )}
        </div>
    );
}

function MonthDayCell({
    day,
    inMonth,
    env,
    barRows,
    hidden,
    onMore,
    children,
}: {
    day: string;
    inMonth: boolean;
    env: GridEnv;
    barRows: number;
    hidden: number;
    onMore: (anchor: HTMLElement) => void;
    children: ReactNode;
}) {
    const { setNodeRef, isOver } = useDayDrop(day);
    const off = dayOffInfo(day, env.holidays);
    const isToday = day === env.today;
    const createHint = env.onCreate ? "Клик по пустому месту — новая задача с этим сроком" : undefined;
    return (
        <div
            ref={setNodeRef}
            role="gridcell"
            data-date={day}
            aria-label={shortDate(day)}
            onClick={(event) => {
                if (env.onCreate && isEmptySpotClick(event)) env.onCreate(day);
            }}
            title={[off.title, createHint].filter(Boolean).join("\n") || undefined}
            className={cn(
                "group/day relative min-h-0 min-w-0 overflow-hidden border-l border-line px-1 pb-1",
                off.isOff && "bg-surface-2",
                !inMonth && "opacity-55",
                env.onCreate && "cursor-cell",
                isOver && "bg-accent-soft ring-1 ring-inset ring-accent",
            )}
        >
            <div className="flex items-center gap-1" style={{ height: DAY_HEADER }}>
                <button
                    type="button"
                    onClick={() => env.onOpenPeriod("day", day)}
                    title="Открыть день"
                    aria-label={`Открыть день ${shortDate(day)}`}
                    className={cn(
                        "grid h-[18px] min-w-[18px] place-items-center rounded-full px-1 text-[11px] hover:bg-hover",
                        isToday ? "bg-accent font-semibold text-on-accent hover:bg-accent-hover" : off.isOff ? "text-muted" : "text-secondary",
                    )}
                >
                    {Number(day.slice(8, 10))}
                </button>
                {off.title && <span className="min-w-0 flex-1 truncate text-[9px] text-muted">{off.title}</span>}
                {env.onCreate && (
                    <button
                        type="button"
                        onClick={() => env.onCreate?.(day)}
                        title="Новая задача с этим сроком"
                        aria-label={`Новая задача на ${shortDate(day)}`}
                        className="ml-auto grid size-[18px] place-items-center rounded text-muted opacity-0 group-hover/day:opacity-100 hover:bg-hover hover:text-primary focus-visible:opacity-100"
                    >
                        <Plus size={12} aria-hidden="true" />
                    </button>
                )}
            </div>
            <div className="flex flex-col gap-[2px]" style={{ marginTop: barRows * ITEM_HEIGHT }}>
                {children}
                {hidden > 0 && (
                    <button
                        type="button"
                        data-grid-item=""
                        onClick={(event) => onMore(event.currentTarget)}
                        title="Показать все задачи дня"
                        className="h-[18px] rounded px-1 text-left text-[11px] font-medium text-muted hover:bg-hover hover:text-primary"
                    >
                        ещё {hidden}
                    </button>
                )}
            </div>
        </div>
    );
}
