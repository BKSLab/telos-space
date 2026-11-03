import { useMemo, type ReactNode } from "react";
import { Plus } from "lucide-react";
import { addDateDays, dayOffInfo } from "@/lib/calendar";
import { isMultiDay, isoWeekNumber, layoutLanes, mondayOf, taskSpan } from "@/lib/calendarGrid";
import { cn } from "@/lib/cn";
import type { CalendarGridMilestone, CalendarGridTask } from "@/lib/types";
import { GridMilestoneChip, GridTaskBar, GridTaskCard, type GridEnv } from "./GridItems";
import { ITEM_HEIGHT, WEEKDAYS, groupMilestones, groupSingles, isEmptySpotClick, shortDate, useDayDrop } from "./gridHelpers";

/**
 * Неделя: 7 колонок; сверху многодневные задачи полосами через дни (по дорожкам),
 * ниже — задачи со сроком в этот день карточками. На телефоне — список по дням.
 */
export function GridWeek({
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
    const weekFrom = mondayOf(date);
    const days = useMemo(() => Array.from({ length: 7 }, (_, index) => addDateDays(weekFrom, index)), [weekFrom]);
    const singles = useMemo(() => groupSingles(tasks), [tasks]);
    const milestonesByDay = useMemo(() => groupMilestones(milestones), [milestones]);
    const multi = useMemo(() => tasks.filter(isMultiDay), [tasks]);
    const { segments, laneCount } = useMemo(() => layoutLanes(multi, weekFrom), [multi, weekFrom]);

    if (isPhone) {
        return (
            <div className="flex flex-col gap-2 p-2">
                {days.map((day, index) => {
                    // Многодневная задача — один раз: в день начала или в понедельник, если началась раньше.
                    const starting = multi.filter((task) => {
                        const span = taskSpan(task);
                        return span !== null && (span.start === day || (index === 0 && span.start < day && span.end >= day));
                    });
                    const count = (milestonesByDay.get(day)?.length ?? 0) + starting.length + (singles.get(day)?.length ?? 0);
                    return (
                        <PhoneDay key={day} day={day} env={env} index={index} count={count}>
                            {(milestonesByDay.get(day) ?? []).map((milestone) => (
                                <GridMilestoneChip key={`${milestone.id}:${milestone.title}`} milestone={milestone} variant="full" showProject={env.showSource} />
                            ))}
                            {starting.map((task) => {
                                const span = taskSpan(task);
                                return (
                                    <GridTaskCard
                                        key={task.id}
                                        task={task}
                                        date={day}
                                        env={env}
                                        part={`pw:${day}`}
                                        variant="full"
                                        note={span ? `до ${shortDate(span.end)}` : undefined}
                                    />
                                );
                            })}
                            {(singles.get(day) ?? []).map((task) => (
                                <GridTaskCard key={task.id} task={task} date={day} env={env} part={`pw:${day}`} variant="full" />
                            ))}
                        </PhoneDay>
                    );
                })}
            </div>
        );
    }

    const barsHeight = laneCount * ITEM_HEIGHT + (laneCount > 0 ? 6 : 0);
    return (
        <div className="flex h-full min-h-[480px] flex-col">
            <div className="grid shrink-0 grid-cols-[36px_repeat(7,minmax(0,1fr))] border-b border-line">
                <span className="flex items-center justify-center font-mono text-[10px] text-muted" title={`Неделя ${isoWeekNumber(weekFrom)} по ISO`}>
                    {isoWeekNumber(weekFrom)}
                </span>
                {days.map((day, index) => {
                    const off = dayOffInfo(day, env.holidays);
                    return (
                        <button
                            key={day}
                            type="button"
                            onClick={() => env.onOpenPeriod("day", day)}
                            title={[off.title, "Открыть день"].filter(Boolean).join("\n")}
                            className={cn("flex min-w-0 items-baseline gap-1.5 border-l border-line px-2 py-1.5 text-left hover:bg-hover", off.isOff && "bg-surface-2")}
                        >
                            <span className={cn("text-[10px] font-semibold uppercase", off.isOff ? "text-disabled" : "text-muted")}>{WEEKDAYS[index]}</span>
                            <span className={cn("grid h-5 min-w-5 place-items-center rounded-full px-1 text-[13px] font-semibold", day === env.today ? "bg-accent text-on-accent" : "text-primary")}>
                                {Number(day.slice(8, 10))}
                            </span>
                            {off.title && <span className="min-w-0 truncate text-[10px] text-muted">{off.title}</span>}
                        </button>
                    );
                })}
            </div>
            {laneCount > 0 && (
                <div className="relative shrink-0 border-b border-line" style={{ height: barsHeight }} aria-label="Многодневные задачи недели">
                    <div className="pointer-events-none absolute inset-y-0 right-0 left-[36px]">
                        {segments.map((segment) => (
                            <GridTaskBar
                                key={segment.item.id}
                                task={segment.item}
                                rowFrom={weekFrom}
                                startCol={segment.startCol}
                                span={segment.span}
                                top={3 + segment.lane * ITEM_HEIGHT}
                                clippedStart={segment.clippedStart}
                                clippedEnd={segment.clippedEnd}
                                env={env}
                            />
                        ))}
                    </div>
                </div>
            )}
            <div className="grid min-h-0 flex-1 grid-cols-[36px_repeat(7,minmax(0,1fr))]">
                <span />
                {days.map((day) => (
                    <WeekColumn key={day} day={day} env={env}>
                        {(milestonesByDay.get(day) ?? []).map((milestone) => (
                            <GridMilestoneChip key={`${milestone.id}:${milestone.title}`} milestone={milestone} variant="full" showProject={env.showSource} />
                        ))}
                        {(singles.get(day) ?? []).map((task) => (
                            <GridTaskCard key={task.id} task={task} date={day} env={env} part={`w:${day}`} variant="full" />
                        ))}
                    </WeekColumn>
                ))}
            </div>
        </div>
    );
}

function WeekColumn({ day, env, children }: { day: string; env: GridEnv; children: ReactNode }) {
    const { setNodeRef, isOver } = useDayDrop(day);
    const off = dayOffInfo(day, env.holidays);
    return (
        <div
            ref={setNodeRef}
            data-date={day}
            onClick={(event) => {
                if (env.onCreate && isEmptySpotClick(event)) env.onCreate(day);
            }}
            title={env.onCreate ? "Клик по пустому месту — новая задача с этим сроком" : undefined}
            className={cn(
                "group/day flex min-h-0 min-w-0 flex-col gap-1 border-l border-line p-1",
                off.isOff && "bg-surface-2",
                env.onCreate && "cursor-cell",
                isOver && "bg-accent-soft ring-1 ring-inset ring-accent",
            )}
        >
            {children}
            {env.onCreate && (
                <button
                    type="button"
                    onClick={() => env.onCreate?.(day)}
                    aria-label={`Новая задача на ${shortDate(day)}`}
                    title="Новая задача с этим сроком"
                    className="flex h-7 items-center justify-center rounded border border-dashed border-line text-muted opacity-0 group-hover/day:opacity-100 hover:border-line-strong hover:text-primary focus-visible:opacity-100"
                >
                    <Plus size={13} aria-hidden="true" />
                </button>
            )}
        </div>
    );
}

function PhoneDay({ day, env, index, count, children }: { day: string; env: GridEnv; index: number; count: number; children: ReactNode }) {
    const { setNodeRef, isOver } = useDayDrop(day);
    const off = dayOffInfo(day, env.holidays);
    return (
        <section
            ref={setNodeRef}
            data-date={day}
            className={cn(
                "rounded-[var(--radius-card)] border border-line p-2",
                off.isOff ? "bg-surface-2" : "bg-surface",
                day === env.today && "border-accent",
                isOver && "ring-1 ring-accent",
            )}
        >
            <header className="mb-1.5 flex items-center gap-2">
                <button type="button" onClick={() => env.onOpenPeriod("day", day)} className="flex min-h-11 items-center gap-1.5 text-left" title="Открыть день">
                    <span className="text-[10px] font-semibold text-muted uppercase">{WEEKDAYS[index]}</span>
                    <span className={cn("text-[13px] font-semibold", day === env.today ? "text-accent" : "text-primary")}>{shortDate(day)}</span>
                </button>
                {off.title && <span className="truncate text-[10px] text-muted">{off.title}</span>}
                {env.onCreate && (
                    <button type="button" onClick={() => env.onCreate?.(day)} className="ml-auto grid size-11 shrink-0 place-items-center rounded text-muted hover:bg-hover" aria-label={`Новая задача на ${shortDate(day)}`} title="Новая задача с этим сроком">
                        <Plus size={14} aria-hidden="true" />
                    </button>
                )}
            </header>
            <div className="flex flex-col gap-1">
                {children}
                {count === 0 && <p className="text-[11px] text-muted">Нет задач</p>}
            </div>
        </section>
    );
}
