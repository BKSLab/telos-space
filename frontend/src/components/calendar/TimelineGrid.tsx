import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { dataColor } from "@/lib/dataColor";
import { useDroppable } from "@dnd-kit/core";
import { CalendarDays, ChevronDown, ChevronRight } from "lucide-react";
import {
    timelineDateDropId,
    calendarDaysBetween,
    timelineBarGeometry,
    timelineDependencyGeometry,
    type CalendarDayModel,
    type TimelineRange,
    type TimelineScale,
} from "@/lib/calendar";
import { cn } from "@/lib/cn";
import { criticalState } from "@/lib/ganttSchedule";
import {
    assigneeGroupKey,
    buildTimelineAssigneeRows,
    buildTimelineStageRows,
    buildTimelineWbsGroupRows,
    type TimelineGroupBy,
    type TimelineGroupKey,
} from "@/lib/timelineGroups";
import { workloadSummary, workloadSummaryText, type WorkloadPerson } from "@/lib/workload";
import type {
    CalendarMilestone,
    CalendarStage,
    CalendarWbsNode,
    CalendarTask,
    ScenarioNormalizedChange,
    TaskDependency,
} from "@/lib/types";
import { TimelineTaskBar } from "@/components/calendar/TimelineTaskBar";
import { StageSpanBracket } from "@/components/calendar/gantt/StageSpanBracket";
import { useCtrlWheelZoom } from "@/components/calendar/gantt/useCtrlWheelZoom";
import { WorkloadStrip } from "@/components/calendar/TimelineWorkload";
import { IconButton } from "@/components/ui/Button";
import { taskColor, type ColorMode } from "@/lib/calendarGrid";
import { useMediaQuery } from "@/lib/useCalendarView";
import { shortDate } from "@/components/calendar/grid/gridHelpers";
import {
    GANTT_COLUMNS,
    LABEL_WIDTH_DEFAULT,
    assigneeInitials,
    clampLabelWidth,
    ganttCellValue,
    ganttColumnsWidth,
    TIMELINE_DAY_WIDTH,
    type GanttColumn,
} from "@/lib/ganttView";

const MONTH_SHORT = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"];

/** Месяцы видимого периода с шириной в днях — подписи шапки для квартала и года. */
function monthBands(days: CalendarDayModel[]) {
    const bands: { key: string; label: string; first: string; count: number }[] = [];
    for (const day of days) {
        const key = day.date.slice(0, 7);
        const last = bands.at(-1);
        if (last && last.key === key) last.count += 1;
        else bands.push({ key, label: `${MONTH_SHORT[Number(day.date.slice(5, 7)) - 1]} ${day.date.slice(0, 4)}`, first: day.date, count: 1 });
    }
    return bands;
}

function TimelineDropColumn({
    day,
    width,
    height,
    scale,
}: {
    day: CalendarDayModel;
    width: number;
    height: number;
    scale: TimelineScale;
}) {
    const { isOver, setNodeRef } = useDroppable({ id: timelineDateDropId(day.date) });
    // На годовой шкале деление — неделя: граница только перед понедельником, затеняются праздники,
    // а обычные выходные не рисуются (при 4 px на день они сливаются в штриховку).
    const isSunday = new Date(`${day.date}T00:00:00`).getDay() === 0;
    const shaded = scale === "year" ? day.isOff && day.holidayTitle !== null : day.isOff;
    return (
        <div
            ref={setNodeRef}
            aria-hidden="true"
            title={day.holidayTitle ?? undefined}
            className={cn(
                scale !== "year" || isSunday ? "border-r border-line-subtle" : undefined,
                shaded && "bg-surface-2/60",
                isOver && "bg-accent/15 ring-1 ring-inset ring-accent",
            )}
            style={{ width, height }}
        />
    );
}

interface TimelineGridProps {
    days: CalendarDayModel[];
    range: TimelineRange;
    scale: TimelineScale;
    tasks: CalendarTask[];
    wbsNodes?: CalendarWbsNode[];
    stagesById: Map<number, CalendarStage>;
    milestones: CalendarMilestone[];
    dependencies: TaskDependency[];
    scenarioChanges: ScenarioNormalizedChange[];
    selectedDate: string;
    /** Клик по дню шапки — «day», по месяцу — «month»: открыть этот период в сетке. */
    onSelectDate: (date: string, scale: "day" | "month") => void;
    onOpenTask: (taskId: number) => void;
    onScheduleTask: (task: CalendarTask) => void;
    onResizeTask: (task: CalendarTask, edge: "start" | "end", deltaDays: number) => void;
    /** «Цвет по» — как в сетке; нет — полосы нейтральные. */
    colorMode?: ColorMode;
    /** Ширина колонки названий (180–480) и её смена перетаскиванием правой границы в шапке. */
    labelWidth?: number;
    onLabelWidthChange?: (width: number) => void;
    /** Дополнительные колонки слева: исполнитель, начало, срок, длительность. */
    columns?: GanttColumn[];
    /** День, к которому прокрутить шкалу (общая дата в «Рядом»); нет — к сегодняшнему. */
    focusDate?: string;
    /** Ctrl+колесо над шкалой: +1 — мельче, −1 — крупнее. */
    onZoom?: (direction: number) => void;
    /** Своя ширина дня (режим показа растягивает шкалу на экран); нет — по масштабу. */
    dayWidth?: number;
    /** Лист для показа и выгрузки: без своей прокрутки, ручек и кнопок — в натуральную ширину. */
    staticLayout?: boolean;
    /** Режим «Критический путь»: подсветить критические полосы и стрелки. */
    criticalMode?: boolean;
    /** Связь протягиванием от ручки полосы; нет — ручек нет. */
    onLinkTasks?: (predecessorId: number, successorId: number) => void;
    /** Строки по WBS, стадиям или по исполнителям с тепловой полосой загрузки. */
    groupBy?: TimelineGroupBy;
    /** Загрузка людей по подписи исполнителя (ответ /calendar/workload). */
    workload?: ReadonlyMap<string, WorkloadPerson>;
}

export function TimelineGrid({
    days,
    range,
    scale,
    tasks,
    wbsNodes = [],
    stagesById,
    milestones,
    dependencies,
    scenarioChanges,
    selectedDate,
    onSelectDate,
    onOpenTask,
    onScheduleTask,
    onResizeTask,
    colorMode,
    labelWidth = LABEL_WIDTH_DEFAULT,
    onLabelWidthChange,
    columns = [],
    focusDate,
    onZoom,
    staticLayout = false,
    dayWidth: dayWidthOverride,
    criticalMode = false,
    onLinkTasks,
    groupBy = "stage",
    workload,
}: TimelineGridProps) {
    const compactScreen = useMediaQuery("(max-width: 1023px)");
    const touchScreen = useMediaQuery("(pointer: coarse)");
    const compact = compactScreen && !staticLayout;
    const pointerEditing = !staticLayout && !compact && !touchScreen;
    const [dragLabelWidth, setDragLabelWidth] = useState<number | null>(null);
    const titleWidth = compact ? 168 : dragLabelWidth ?? labelWidth;
    // Дополнительные колонки можно пролистать целиком: широкая закреплённая часть не закрывает шкалу телефона.
    const labelPosition = compact && columns.length > 0 ? "relative" : "sticky left-0";
    const visibleColumns = GANTT_COLUMNS.filter((column) => columns.includes(column.id));
    const leftWidth = titleWidth + ganttColumnsWidth(columns);
    const [collapsedIds, setCollapsedIds] = useState<Set<TimelineGroupKey>>(new Set());
    const stages = useMemo(() => [...stagesById.values()], [stagesById]);
    // Строки сгруппированы по стадиям проекта (шкала читается так же, как канбан)
    // или по исполнителям — тогда в заголовке человека тепловая полоса загрузки.
    const rows = useMemo(
        () =>
            groupBy === "assignee"
                ? buildTimelineAssigneeRows(tasks, collapsedIds, workload ? [...workload.keys()] : [])
                : groupBy === "wbs" ? buildTimelineWbsGroupRows(wbsNodes, tasks, collapsedIds) : buildTimelineStageRows(stages, tasks, collapsedIds),
        [collapsedIds, groupBy, tasks, stages, wbsNodes, workload],
    );
    const dayWidth = dayWidthOverride ?? TIMELINE_DAY_WIDTH[scale];
    const showInitials = scale === "week" || scale === "month";
    const timelineWidth = days.length * dayWidth;
    const rowHeight = compact ? 64 : scale === "week" ? 48 : 42;
    const headerHeight = 42;
    const bodyHeight = Math.max(rows.length * rowHeight, 92);
    const scenarioByTaskId = useMemo(
        () => new Map(scenarioChanges.map((change) => [change.task_id, change])),
        [scenarioChanges],
    );
    const dependencyPaths = useMemo(() => {
        const rowByTaskId = new Map<number, number>();
        rows.forEach((row, index) => {
            if (row.kind === "task") rowByTaskId.set(row.task.id, index);
        });
        const taskById = new Map(tasks.map((task) => [task.id, task]));
        return dependencies.flatMap((dependency) => {
            const predecessor = taskById.get(dependency.predecessor_task_id);
            const successor = taskById.get(dependency.successor_task_id);
            const predecessorRow = rowByTaskId.get(dependency.predecessor_task_id);
            const successorRow = rowByTaskId.get(dependency.successor_task_id);
            if (
                predecessor === undefined ||
                successor === undefined ||
                predecessorRow === undefined ||
                successorRow === undefined
            ) {
                return [];
            }
            const predecessorBar = timelineBarGeometry(predecessor, range, dayWidth);
            const successorBar = timelineBarGeometry(successor, range, dayWidth);
            if (predecessorBar === null || successorBar === null) return [];
            return [
                {
                    dependency,
                    geometry: timelineDependencyGeometry(
                        predecessorBar.left + predecessorBar.width,
                        predecessorRow * rowHeight + rowHeight / 2,
                        successorBar.left,
                        successorRow * rowHeight + rowHeight / 2,
                    ),
                    predecessor,
                    successor,
                },
            ];
        });
    }, [dayWidth, dependencies, range, rowHeight, rows, tasks]);

    // Широкая шкала (квартал, год) открывается так, чтобы «сегодня» было в поле зрения.
    const scrollRef = useRef<HTMLDivElement>(null);
    const todayIndex = days.findIndex((day) => day.isToday);
    const focusIndex = focusDate ? days.findIndex((day) => day.date === focusDate) : -1;
    const scrollIndex = focusIndex >= 0 ? focusIndex : todayIndex;
    useEffect(() => {
        const element = scrollRef.current;
        if (!element || staticLayout) return;
        element.scrollLeft = scrollIndex < 0 ? 0 : Math.max(0, scrollIndex * dayWidth - (element.clientWidth - leftWidth) / 3);
        // leftWidth не в зависимостях: смена ширины колонки не должна дёргать шкалу.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [dayWidth, range.dateFrom, scrollIndex, staticLayout]);
    useCtrlWheelZoom(scrollRef, staticLayout ? undefined : onZoom);

    function startLabelResize(event: ReactPointerEvent<HTMLSpanElement>) {
        if (!onLabelWidthChange) return;
        event.preventDefault();
        event.stopPropagation();
        const startX = event.clientX;
        const startWidth = labelWidth;
        const move = (moveEvent: PointerEvent) => setDragLabelWidth(clampLabelWidth(startWidth + moveEvent.clientX - startX));
        const finish = (upEvent: PointerEvent) => {
            window.removeEventListener("pointermove", move);
            setDragLabelWidth(null);
            onLabelWidthChange(clampLabelWidth(startWidth + upEvent.clientX - startX));
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", finish, { once: true });
    }

    function toggleRow(id: TimelineGroupKey) {
        setCollapsedIds((current) => {
            const next = new Set(current);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    }

    return (
        <section
            className={cn(
                "instrument-surface flex flex-col rounded-[var(--radius-panel)] border-line-subtle",
                staticLayout ? "w-max" : "h-full min-h-0 overflow-hidden",
            )}
        >
            <div ref={scrollRef} className={staticLayout ? undefined : "scrollbar-thin min-h-0 flex-1 overflow-auto overscroll-contain"} style={{ scrollPaddingTop: headerHeight }} data-timeline-scroll="" data-own-arrows="" tabIndex={staticLayout ? undefined : 0} role="region" aria-label="Шкала задач, прокручивается по горизонтали и вертикали">
                <div className="relative min-w-max" style={{ width: leftWidth + timelineWidth }}>
                    <div
                        className="sticky top-0 z-30 grid border-b border-line bg-surface-2/95 backdrop-blur-sm"
                        style={{ gridTemplateColumns: `${leftWidth}px ${timelineWidth}px`, height: headerHeight }}
                    >
                        <div className={cn(labelPosition, "z-40 flex items-center border-r border-line bg-surface-2 text-[11px] font-semibold tracking-[0.06em] text-muted uppercase")}>
                            <span className="flex min-w-0 flex-1 items-center gap-2 px-3">
                                <CalendarDays size={13} className="shrink-0" /> Работа
                            </span>
                            {visibleColumns.map((column) => (
                                <span key={column.id} className="shrink-0 truncate border-l border-line-subtle px-1 text-[9px]" style={{ width: column.width }} title={column.label}>
                                    {column.header}
                                </span>
                            ))}
                            {onLabelWidthChange && pointerEditing && (
                                <span
                                    role="separator"
                                    aria-orientation="vertical"
                                    aria-label="Ширина колонки задач"
                                    title="Потяните, чтобы изменить ширину колонки с задачами"
                                    onPointerDown={startLabelResize}
                                    className="absolute inset-y-0 z-50 w-1.5 cursor-col-resize hover:bg-accent/40"
                                    style={{ left: titleWidth - 3 }}
                                    data-label-resize=""
                                />
                            )}
                        </div>
                        <div className="relative flex">
                            {(scale === "year" || scale === "quarter") && (
                                <div className="absolute inset-x-0 top-0 z-10 flex h-[18px] border-b border-line-subtle bg-surface-2">
                                    {monthBands(days).map((band) => (
                                        <button
                                            key={band.key}
                                            type="button"
                                            onClick={() => onSelectDate(band.first, "month")}
                                            title={`${band.label} — открыть месяц в сетке`}
                                            className="shrink-0 truncate border-r border-line px-1 text-left text-[10px] font-semibold text-secondary hover:bg-hover hover:text-primary"
                                            style={{ width: band.count * dayWidth }}
                                        >
                                            {band.label}
                                        </button>
                                    ))}
                                </div>
                            )}
                            {scale === "year" && (
                                <div className="absolute inset-x-0 bottom-0 flex h-[24px]" aria-hidden="true">
                                    {days.map((day) => (
                                        <span
                                            key={day.date}
                                            className={cn(
                                                "shrink-0",
                                                new Date(`${day.date}T00:00:00`).getDay() === 0 && "border-r border-line-subtle",
                                                day.isOff && day.holidayTitle !== null && "bg-surface-2",
                                            )}
                                            style={{ width: dayWidth }}
                                        />
                                    ))}
                                </div>
                            )}
                            {days.map((day, index) => {
                                if (scale === "year") return null;
                                const showLabel =
                                    scale !== "quarter" || day.dayNumber === 1 || index % 7 === 0;
                                const label = [day.date, day.holidayTitle, "Открыть день в сетке"].filter(Boolean).join("\n");
                                return (
                                    <button
                                        key={day.date}
                                        type="button"
                                        title={label}
                                        aria-label={`Открыть день ${day.date} в сетке`}
                                        onClick={() => onSelectDate(day.date, "day")}
                                        className={cn(
                                            "shrink-0 border-r border-line-subtle text-[10px] text-muted hover:bg-hover hover:text-primary",
                                            scale === "quarter" && "pt-[18px]",
                                            day.isOff && "bg-surface-2",
                                            day.isToday && "text-accent",
                                            selectedDate === day.date && "bg-accent/10 text-accent",
                                        )}
                                        style={{ width: dayWidth }}
                                    >
                                        {showLabel ? day.dayNumber : ""}
                                        {scale === "week" && (
                                            <span className="ml-1 hidden text-[9px] uppercase sm:inline">
                                                {new Intl.DateTimeFormat("ru-RU", {
                                                    weekday: "short",
                                                }).format(
                                                    new Date(
                                                        Number(day.date.slice(0, 4)),
                                                        Number(day.date.slice(5, 7)) - 1,
                                                        Number(day.date.slice(8, 10)),
                                                    ),
                                                )}
                                            </span>
                                        )}
                                    </button>
                                );
                            })}
                            {days.map(
                                (day, index) =>
                                    day.isToday && (
                                        <span
                                            key={`today-label-${day.date}`}
                                            className="pointer-events-none absolute bottom-0 z-20 -translate-x-1/2 rounded-t-[3px] bg-accent px-1 text-[9px] leading-[13px] font-semibold text-on-accent"
                                            style={{ left: index * dayWidth + dayWidth / 2 }}
                                            title="Сегодня"
                                        >
                                            сегодня
                                        </span>
                                    ),
                            )}
                        </div>
                    </div>

                    <div className="relative" style={{ minHeight: bodyHeight }}>
                        <div
                            className="pointer-events-none absolute top-0 z-0 flex"
                            style={{ left: leftWidth, width: timelineWidth, height: bodyHeight }}
                        >
                            {days.map((day) => (
                                <TimelineDropColumn
                                    key={day.date}
                                    day={day}
                                    width={dayWidth}
                                    height={bodyHeight}
                                    scale={scale}
                                />
                            ))}
                        </div>
                        {days.map(
                            (day, index) =>
                                day.isToday && (
                                    <div
                                        key={`today-${day.date}`}
                                        aria-label="Сегодня"
                                        className="pointer-events-none absolute top-0 z-20 w-0.5 bg-accent"
                                        style={{
                                            left: leftWidth + index * dayWidth + dayWidth / 2,
                                            height: bodyHeight,
                                        }}
                                    />
                                ),
                        )}
                        {milestones.map((milestone) => {
                            const offset =
                                calendarDaysBetween(range.dateFrom, milestone.due_date) * dayWidth +
                                dayWidth / 2;
                            return (
                                <div
                                    key={`${milestone.is_system ? "system" : milestone.id}:${milestone.due_date}`}
                                    className={cn(
                                        "pointer-events-none absolute top-0 z-20 border-l border-dashed",
                                        milestone.status === "ACHIEVED"
                                            ? "border-success"
                                            : "border-purple",
                                    )}
                                    style={{ left: leftWidth + offset, height: bodyHeight }}
                                    title={`${milestone.title} · ${milestone.due_date}`}
                                >
                                    <span
                                        className={cn(
                                            "absolute -top-1.5 -left-1 size-2 rotate-45 border",
                                            milestone.status === "ACHIEVED"
                                                ? "border-success bg-success"
                                                : "border-purple bg-surface",
                                        )}
                                    />
                                    <span className="absolute top-1 left-1 max-w-28 truncate rounded bg-surface-2/90 px-1 py-0.5 text-[9px] text-purple">
                                        ◆ {milestone.title}
                                    </span>
                                </div>
                            );
                        })}
                        {dependencyPaths.length > 0 && (
                            <svg
                                aria-label="Зависимости задач Finish-to-Start"
                                className="pointer-events-none absolute top-0 z-[5] overflow-visible text-accent"
                                style={{ left: leftWidth }}
                                width={timelineWidth}
                                height={bodyHeight}
                                viewBox={`0 0 ${timelineWidth} ${bodyHeight}`}
                            >
                                <defs>
                                    <marker
                                        id="timeline-dependency-arrow"
                                        markerWidth="6"
                                        markerHeight="6"
                                        refX="5"
                                        refY="3"
                                        orient="auto"
                                    >
                                        <path d="M 0 0 L 6 3 L 0 6 z" fill="currentColor" />
                                    </marker>
                                    <marker
                                        id="timeline-dependency-arrow-critical"
                                        markerWidth="6"
                                        markerHeight="6"
                                        refX="5"
                                        refY="3"
                                        orient="auto"
                                    >
                                        <path d="M 0 0 L 6 3 L 0 6 z" fill="var(--color-danger)" />
                                    </marker>
                                </defs>
                                {dependencyPaths.map(
                                    ({ dependency, geometry, predecessor, successor }) => (
                                        <path
                                            key={dependency.id}
                                            d={geometry.path}
                                            fill="none"
                                            stroke={criticalMode && dependency.is_critical ? "var(--color-danger)" : "currentColor"}
                                            strokeWidth={criticalMode && dependency.is_critical ? "2" : "1.25"}
                                            data-critical={criticalMode ? (dependency.is_critical ? "critical" : "dimmed") : undefined}
                                            strokeDasharray={
                                                dependency.lag_days > 0 ? "4 3" : undefined
                                            }
                                            markerEnd={criticalMode && dependency.is_critical ? "url(#timeline-dependency-arrow-critical)" : "url(#timeline-dependency-arrow)"}
                                            opacity={criticalMode ? (dependency.is_critical ? "0.95" : "0.18") : "0.62"}
                                        >
                                            <title>
                                                {predecessor.key} → {successor.key}
                                                {dependency.lag_days > 0
                                                    ? `, задержка ${dependency.lag_days} дн.`
                                                    : ""}
                                            </title>
                                        </path>
                                    ),
                                )}
                            </svg>
                        )}

                        {rows.map((row) => {
                            if (row.kind !== "task") {
                                const aggregateStart = row.aggregate.startDate;
                                const aggregateEnd = row.aggregate.dueDate;
                                const visibleStart =
                                    aggregateStart && aggregateStart < range.dateFrom
                                        ? range.dateFrom
                                        : aggregateStart;
                                const visibleEnd =
                                    aggregateEnd && aggregateEnd > range.dateTo
                                        ? range.dateTo
                                        : aggregateEnd;
                                const left = visibleStart
                                    ? calendarDaysBetween(range.dateFrom, visibleStart) * dayWidth
                                    : 0;
                                const width =
                                    visibleStart && visibleEnd
                                        ? (calendarDaysBetween(visibleStart, visibleEnd) + 1) *
                                          dayWidth
                                        : 0;
                                const isPerson = row.assignee !== undefined;
                                const collapseKey: TimelineGroupKey = isPerson
                                    ? assigneeGroupKey(row.assignee ?? null)
                                    : (row.groupKey ?? row.stageId ?? "other");
                                const person = isPerson && row.assignee ? workload?.get(row.assignee) : undefined;
                                const personSummary = isPerson
                                    ? workloadSummaryText(workloadSummary(person, row.aggregate.total))
                                    : null;
                                return (
                                    <div
                                        key={row.id}
                                        className="relative grid border-b border-line bg-surface-2/55"
                                        style={{
                                            gridTemplateColumns: `${leftWidth}px ${timelineWidth}px`,
                                            height: rowHeight,
                                        }}
                                    >
                                        <button
                                            type="button"
                                            aria-expanded={!row.collapsed}
                                            onClick={() => toggleRow(collapseKey)}
                                            title={
                                                personSummary
                                                    ? `${row.title}\n${personSummary.title}\n${row.collapsed ? "Показать задачи" : "Свернуть задачи"}`
                                                    : row.collapsed
                                                      ? "Показать задачи группы"
                                                      : "Свернуть задачи группы"
                                            }
                                            className={cn(labelPosition, "z-20 flex min-w-0 items-center gap-1.5 border-r border-line bg-surface-2 px-2 text-left")}
                                            style={{ paddingLeft: compact ? 8 + Math.min(row.depth ?? 0, 2) * 8 : 8 + (row.depth ?? 0) * 14 }}
                                        >
                                            {row.collapsed ? (
                                                <ChevronRight size={13} className="shrink-0" />
                                            ) : (
                                                <ChevronDown size={13} className="shrink-0" />
                                            )}
                                            <span
                                                aria-hidden="true"
                                                className="size-2 shrink-0 rounded-full"
                                                style={{ background: dataColor(row.color) ?? "var(--color-text-muted)" }}
                                            />
                                            <span className={cn("min-w-0 flex-1 text-[11px] font-medium text-primary", compact ? "line-clamp-2" : "truncate")}>
                                                {row.title}
                                            </span>
                                            <span className="shrink-0 font-mono text-[9px] text-muted">
                                                {personSummary ? (
                                                    personSummary.short
                                                ) : compact ? row.aggregate.total : (
                                                    <>
                                                        {row.aggregate.total} · {row.aggregate.progress}% ·{" "}
                                                        {row.aggregate.risks} риск.
                                                    </>
                                                )}
                                            </span>
                                        </button>
                                        <div className="relative" style={{ width: timelineWidth }}>
                                            {person && (
                                                <WorkloadStrip
                                                    person={person}
                                                    range={range}
                                                    dayWidth={dayWidth}
                                                    height={rowHeight}
                                                />
                                            )}
                                            {!isPerson && row.collapsed && width > 0 && (
                                                <div
                                                    className="absolute top-2 h-5 rounded border border-accent-border/60 bg-accent/15 px-2 text-[9px] leading-5 text-accent shadow-card"
                                                    style={{ left, width }}
                                                    title={`${row.title}: ${row.aggregate.total} задач, ${row.aggregate.progress}% завершено, рисков: ${row.aggregate.risks}`}
                                                >
                                                    <span className="block truncate">
                                                        {row.aggregate.total} задач ·{" "}
                                                        {row.aggregate.progress}%
                                                    </span>
                                                </div>
                                            )}
                                            {!row.collapsed && (
                                                <StageSpanBracket
                                                    left={left}
                                                    width={width}
                                                    tone={dataColor(row.color) ?? "var(--color-text-muted)"}
                                                    title={`${row.title}: от ${aggregateStart ?? "—"} до ${aggregateEnd ?? "—"}, ${row.aggregate.total} задач, ${row.aggregate.progress}% завершено`}
                                                />
                                            )}
                                        </div>
                                    </div>
                                );
                            }
                            const task = row.task;
                            const geometry = timelineBarGeometry(task, range, dayWidth);
                            const baselineGeometry = task.baseline_due_date
                                ? timelineBarGeometry(
                                      {
                                          ...task,
                                          start_date: task.baseline_start_date,
                                          due_date: task.baseline_due_date,
                                      },
                                      range,
                                      dayWidth,
                                  )
                                : null;
                            const scenarioChange = scenarioByTaskId.get(task.id);
                            const scenarioGeometry = scenarioChange
                                ? timelineBarGeometry(
                                      {
                                          ...task,
                                          start_date: scenarioChange.proposed.start_date,
                                          due_date: scenarioChange.proposed.due_date,
                                      },
                                      range,
                                      dayWidth,
                                  )
                                : null;
                            return (
                                <div
                                    key={row.id}
                                    className="relative grid border-b border-line-subtle last:border-b-0"
                                    style={{
                                        gridTemplateColumns: `${leftWidth}px ${timelineWidth}px`,
                                        height: rowHeight,
                                    }}
                                >
                                    <div
                                        className={cn(labelPosition, "z-20 flex min-w-0 items-center gap-1 border-r border-line bg-surface px-2")}
                                        style={{ paddingLeft: compact ? 8 + Math.min(row.depth ?? 0, 2) * 8 : 22 + (row.depth ?? 0) * 14 }}
                                    >
                                        <button
                                            type="button"
                                            onClick={() => onOpenTask(task.id)}
                                            title={`${task.key} · ${task.title}\n${task.start_date ?? "—"} — ${task.due_date ?? "без срока"}`}
                                            className={cn("min-w-0 flex-1 text-left text-[11px] text-secondary hover:text-primary", compact ? "min-h-11" : "truncate")}
                                        >
                                            <span className={compact ? "line-clamp-2" : undefined}>
                                            <span className="font-mono text-[10px] text-accent">
                                                {task.key}
                                            </span>{" "}
                                            {task.title}
                                            </span>
                                            {compact && <span className="mt-0.5 block truncate text-[10px] text-muted">{task.start_date ? shortDate(task.start_date) : "—"} → {task.due_date ? shortDate(task.due_date) : "без срока"}</span>}
                                        </button>
                                        {task.drift_days !== null && task.drift_days !== 0 && (
                                            <span
                                                className={cn(
                                                    "shrink-0 font-mono text-[9px]",
                                                    task.drift_days > 0
                                                        ? "text-danger"
                                                        : "text-success",
                                                )}
                                                title={`Отклонение от baseline: ${task.drift_days} дн.`}
                                            >
                                                {task.drift_days > 0 ? "+" : ""}
                                                {task.drift_days}д
                                            </span>
                                        )}
                                        {!staticLayout && (
                                            <IconButton
                                                size="sm"
                                                className={compact ? "size-11 shrink-0" : undefined}
                                                label={`Изменить даты ${task.key}`}
                                                onClick={() => onScheduleTask(task)}
                                            >
                                                <CalendarDays size={11} />
                                            </IconButton>
                                        )}
                                        {visibleColumns.map((column) => (
                                            <span
                                                key={column.id}
                                                className="shrink-0 truncate px-1 font-mono text-[10px] text-muted"
                                                style={{ width: column.width }}
                                                title={`${column.label}: ${ganttCellValue(task, column.id)}`}
                                                data-gantt-cell={column.id}
                                            >
                                                {ganttCellValue(task, column.id)}
                                            </span>
                                        ))}
                                    </div>
                                    <div className="relative" style={{ width: timelineWidth }}>
                                        {baselineGeometry && (
                                            <div
                                                className="absolute bottom-1 z-10 h-1 rounded-full border border-dashed border-line-strong bg-surface-2 opacity-75"
                                                style={{
                                                    left: baselineGeometry.left,
                                                    width: baselineGeometry.width,
                                                }}
                                                title={`Baseline: ${task.baseline_start_date ?? task.baseline_due_date} — ${task.baseline_due_date}`}
                                            />
                                        )}
                                        {geometry && (
                                            <TimelineTaskBar
                                                task={task}
                                                stage={stagesById.get(task.stage_id)}
                                                geometry={geometry}
                                                dayWidth={dayWidth}
                                                timelineWidth={timelineWidth}
                                                scale={scale}
                                                onOpen={onOpenTask}
                                                onResize={onResizeTask}
                                                color={colorMode ? taskColor(task, colorMode, stagesById) : undefined}
                                                initials={showInitials ? assigneeInitials(task.assignee) : null}
                                                readOnly={!pointerEditing}
                                                critical={criticalState(task.is_critical, criticalMode)}
                                                onLink={pointerEditing ? onLinkTasks : undefined}
                                            />
                                        )}
                                        {scenarioGeometry && scenarioChange && (
                                            <div
                                                aria-label={`Предложенный интервал ${task.key}`}
                                                className="pointer-events-none absolute top-1.5 z-[15] flex h-8 items-center overflow-hidden rounded-[var(--radius-control)] border border-ai-violet/65 px-2 text-[9px] font-semibold text-ai-violet shadow-card"
                                                style={{
                                                    left: scenarioGeometry.left,
                                                    width: scenarioGeometry.width,
                                                    backgroundImage:
                                                        "repeating-linear-gradient(135deg, transparent 0 6px, color-mix(in srgb, var(--color-ai-violet) 16%, transparent) 6px 10px)",
                                                }}
                                                title={`PROPOSED · ${scenarioChange.proposed.start_date ?? scenarioChange.proposed.due_date ?? "—"} — ${scenarioChange.proposed.due_date ?? "—"}`}
                                            >
                                                <span className="truncate">PROPOSED</span>
                                            </div>
                                        )}
                                    </div>
                                </div>
                            );
                        })}
                        {rows.length === 0 && (
                            <div className="absolute inset-0 flex items-center justify-center text-[12px] text-muted" style={{ left: leftWidth }}>
                                В этом диапазоне нет запланированных задач
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </section>
    );
}
