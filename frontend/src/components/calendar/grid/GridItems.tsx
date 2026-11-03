import { useEffect, useLayoutEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useDraggable } from "@dnd-kit/core";
import { CalendarDays, Check, Diamond } from "lucide-react";
import { IconButton } from "@/components/ui/Button";
import { addDateDays, type HolidayMap } from "@/lib/calendar";
import { taskColor, type ColorMode, type GridScale } from "@/lib/calendarGrid";
import { cn } from "@/lib/cn";
import type { CalendarGridMilestone, CalendarGridTask, CalendarStage } from "@/lib/types";
import { milestoneTooltip, rememberGridGrab, shortDate, stateLabel, taskTooltip } from "./gridHelpers";

/** Общее окружение сетки: как красить, что можно тянуть, куда вести клики. */
export interface GridEnv {
    today: string;
    holidays: HolidayMap;
    colorMode: ColorMode;
    stagesById?: ReadonlyMap<number, CalendarStage>;
    canDrag: (task: CalendarGridTask) => boolean;
    /** Показывать чип источника (ключ проекта) — в «Моём календаре». */
    showSource: boolean;
    onOpenTask: (taskId: number) => void;
    /** Выбор дат без перетаскивания, в том числе на сенсорном экране. */
    onScheduleTask?: (task: CalendarGridTask) => void;
    /** Создать задачу с этим сроком; нет — клик по пустому дню ничего не делает. */
    onCreate?: (date: string) => void;
    /** Перейти к масштабу на дату (номер дня → «День», номер недели → «Неделя», день года → «Месяц»). */
    onOpenPeriod: (scale: GridScale, date: string) => void;
}

/**
 * Данные перетаскивания карточки сетки: `date` — день карточки (для полосы — первый видимый
 * день отрезка). Точный день, за который взяли полосу, пишется в gridGrab при нажатии.
 */
export interface GridDragData {
    task: CalendarGridTask;
    dragKind: "grid";
    date: string;
}

function SourceChip({ task }: { task: CalendarGridTask }) {
    if (!task.source) return null;
    const label = task.source.key ?? (task.source.kind === "PERSONAL" ? "Разовая" : task.source.name);
    return (
        <span
            className="shrink-0 rounded-[3px] px-1 font-mono text-[9px] leading-[14px] text-muted"
            style={{ background: "color-mix(in srgb, var(--color-text-muted) 14%, transparent)" }}
        >
            {label}
        </span>
    );
}

function useGridDrag(task: CalendarGridTask, part: string, date: string, env: GridEnv) {
    const id = `grid-task:${task.id}:${part}`;
    const data: GridDragData = { task, dragKind: "grid", date };
    return { ...useDraggable({ id, data, disabled: !env.canDrag(task) }), id };
}

/** Карточка задачи в клетке/колонке сетки. */
export function GridTaskCard({
    task,
    date,
    env,
    part,
    variant = "compact",
    note,
}: {
    task: CalendarGridTask;
    date: string;
    env: GridEnv;
    part: string;
    variant?: "compact" | "full";
    note?: string;
}) {
    const { attributes, listeners, setNodeRef, isDragging } = useGridDrag(task, part, date, env);
    const color = taskColor(task, env.colorMode, env.stagesById);
    const stageName = task.stage_id === null ? undefined : env.stagesById?.get(task.stage_id)?.name;
    const draggable = env.canDrag(task);
    return (
        <div className="flex min-w-0 items-stretch gap-1" data-grid-item="">
        <button
            ref={setNodeRef}
            type="button"
            data-grid-item=""
            onClick={(event) => {
                event.stopPropagation();
                env.onOpenTask(task.id);
            }}
            title={taskTooltip(task, stageName)}
            aria-label={`${task.key}: ${task.title}, ${stateLabel(task)}`}
            className={cn(
                "flex w-full min-w-0 items-center gap-1 rounded-[5px] border text-left",
                "transition-[background-color,border-color,opacity] duration-[var(--duration-fast)]",
                variant === "compact" ? "h-[18px] pr-1 text-[11px]" : "min-h-12 py-1.5 pr-2 text-[12px] lg:min-h-8 lg:py-1",
                task.is_overdue && !task.is_done
                    ? "border-danger bg-danger/10 text-primary"
                    : "border-line bg-elevated text-secondary hover:border-line-strong hover:text-primary",
                task.is_done && "opacity-60",
                draggable && "cursor-grab touch-none active:cursor-grabbing",
                isDragging && "opacity-35",
            )}
            style={{ borderLeft: `3px solid ${color}` }}
            {...(draggable ? attributes : {})}
            {...(draggable ? listeners : {})}
        >
            {task.is_done && <Check size={11} aria-hidden="true" className="ml-1 shrink-0 text-success" />}
            <span className={cn("shrink-0 pl-1 font-mono text-[10px] text-muted", task.is_done && "line-through")}>{task.key}</span>
            <span className={cn("min-w-0 flex-1", task.is_done && "line-through")}>
                <span className={variant === "full" ? "line-clamp-2 break-words lg:line-clamp-none lg:truncate" : "block truncate"}>{task.title}</span>
                {variant === "full" && <span className="mt-0.5 block text-[11px] text-muted lg:hidden">{note ?? (task.due_date ? `до ${shortDate(task.due_date)}` : task.start_date ? `с ${shortDate(task.start_date)}` : "Без срока")}</span>}
            </span>
            {variant === "full" && note && <span className="hidden shrink-0 text-[10px] text-muted lg:inline">{note}</span>}
            {env.showSource && <SourceChip task={task} />}
        </button>
        {variant === "full" && env.onScheduleTask && <IconButton className="min-h-11 w-11 shrink-0 self-center lg:min-h-7 lg:w-7" size="sm" label={`Изменить даты ${task.key}`} onClick={(event) => {
            event.stopPropagation();
            env.onScheduleTask?.(task);
        }}><CalendarDays size={16} aria-hidden="true" /></IconButton>}
        </div>
    );
}

/** Отрезок многодневной задачи в строке недели: тянется через дни, начало — за ячейкой `rowFrom + startCol`. */
export function GridTaskBar({
    task,
    rowFrom,
    startCol,
    span,
    top,
    clippedStart,
    clippedEnd,
    env,
    columns = 7,
}: {
    task: CalendarGridTask;
    rowFrom: string;
    startCol: number;
    span: number;
    top: number;
    clippedStart: boolean;
    clippedEnd: boolean;
    env: GridEnv;
    columns?: number;
}) {
    const segmentStart = addDateDays(rowFrom, startCol);
    const { attributes, listeners, setNodeRef, isDragging, id } = useGridDrag(task, `bar:${rowFrom}`, segmentStart, env);
    const color = taskColor(task, env.colorMode, env.stagesById);
    const stageName = task.stage_id === null ? undefined : env.stagesById?.get(task.stage_id)?.name;
    const draggable = env.canDrag(task);

    function rememberGrabDay(event: ReactPointerEvent<HTMLButtonElement>) {
        const rect = event.currentTarget.getBoundingClientRect();
        const column = Math.min(span - 1, Math.max(0, Math.floor(((event.clientX - rect.left) / rect.width) * span)));
        rememberGridGrab(id, addDateDays(segmentStart, column));
    }

    return (
        <button
            ref={setNodeRef}
            type="button"
            data-grid-item=""
            onPointerDownCapture={rememberGrabDay}
            onClick={(event) => {
                event.stopPropagation();
                env.onOpenTask(task.id);
            }}
            title={taskTooltip(task, stageName)}
            aria-label={`${task.key}: ${task.title}, ${stateLabel(task)}`}
            className={cn(
                "pointer-events-auto absolute flex h-[18px] min-w-0 items-center gap-1 overflow-hidden border px-1 text-left text-[11px]",
                clippedStart ? "rounded-l-none" : "rounded-l-[5px]",
                clippedEnd ? "rounded-r-none" : "rounded-r-[5px]",
                task.is_overdue && !task.is_done ? "border-danger text-primary" : "border-transparent text-primary",
                task.is_done && "opacity-60",
                draggable && "cursor-grab touch-none active:cursor-grabbing",
                isDragging && "opacity-35",
            )}
            style={{
                top,
                left: `calc(${(startCol / columns) * 100}% + 2px)`,
                width: `calc(${(span / columns) * 100}% - 4px)`,
                background: `color-mix(in srgb, ${color} 24%, var(--color-bg-surface))`,
                borderLeft: clippedStart ? undefined : `3px solid ${color}`,
            }}
            {...(draggable ? attributes : {})}
            {...(draggable ? listeners : {})}
        >
            {clippedStart && <span aria-hidden="true" className="text-muted">‹</span>}
            <span className={cn("shrink-0 font-mono text-[10px] text-secondary", task.is_done && "line-through")}>{task.key}</span>
            <span className={cn("min-w-0 flex-1 truncate", task.is_done && "line-through")}>{task.title}</span>
            {env.showSource && <SourceChip task={task} />}
            {clippedEnd && <span aria-hidden="true" className="text-muted">›</span>}
        </button>
    );
}

export function GridMilestoneChip({ milestone, variant = "compact", showProject }: { milestone: CalendarGridMilestone; variant?: "compact" | "full"; showProject?: boolean }) {
    const achieved = milestone.status === "ACHIEVED";
    return (
        <span
            data-grid-item=""
            title={milestoneTooltip(milestone)}
            className={cn(
                "flex w-full min-w-0 items-center gap-1 rounded-[5px] px-1 text-left",
                variant === "compact" ? "h-[18px] text-[11px]" : "min-h-8 py-1 text-[12px]",
                achieved ? "text-success" : "text-accent",
            )}
            style={{ background: `color-mix(in srgb, var(${achieved ? "--color-success" : "--color-info"}) 12%, transparent)` }}
        >
            <Diamond size={10} aria-hidden="true" className="shrink-0" fill="currentColor" />
            <span className="min-w-0 flex-1 truncate">{milestone.title}</span>
            {showProject && milestone.project && <span className="shrink-0 font-mono text-[9px] text-muted">{milestone.project.key}</span>}
        </span>
    );
}

/**
 * Плавающая панель у элемента (список «ещё N», день на телефоне): fixed-позиция, чтобы её не
 * обрезала прокрутка окна календаря; закрывается по Escape и клику снаружи.
 */
export function AnchoredPanel({
    anchor,
    label,
    onClose,
    children,
    width = 280,
}: {
    anchor: HTMLElement;
    label: string;
    onClose: () => void;
    children: ReactNode;
    width?: number;
}) {
    const panelRef = useRef<HTMLDivElement>(null);
    const [position, setPosition] = useState<{ left: number; top: number } | null>(null);

    useLayoutEffect(() => {
        const update = () => {
            const rect = anchor.getBoundingClientRect();
            const panel = panelRef.current;
            const height = panel?.offsetHeight ?? 200;
            const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
            const below = rect.bottom + 4;
            const top = below + height <= window.innerHeight - 8 ? below : Math.max(8, rect.top - height - 4);
            setPosition({ left, top });
        };
        update();
        window.addEventListener("resize", update);
        window.addEventListener("scroll", update, true);
        return () => {
            window.removeEventListener("resize", update);
            window.removeEventListener("scroll", update, true);
        };
    }, [anchor, width]);

    useEffect(() => {
        const onPointerDown = (event: PointerEvent) => {
            const target = event.target as Node;
            if (!panelRef.current?.contains(target) && !anchor.contains(target)) onClose();
        };
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                event.preventDefault();
                onClose();
                anchor.focus();
            }
        };
        window.addEventListener("pointerdown", onPointerDown);
        window.addEventListener("keydown", onKeyDown);
        return () => {
            window.removeEventListener("pointerdown", onPointerDown);
            window.removeEventListener("keydown", onKeyDown);
        };
    }, [anchor, onClose]);

    return createPortal(
        <div
            ref={panelRef}
            role="dialog"
            aria-label={label}
            className="material-glass fixed z-[60] max-h-[min(420px,70dvh)] overflow-y-auto rounded-[var(--radius-panel)] p-2 shadow-panel"
            style={{ width, ...(position ?? { visibility: "hidden" as const }) }}
        >
            {children}
        </div>,
        document.body,
    );
}
