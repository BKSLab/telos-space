import { useMemo, useState, type KeyboardEvent } from "react";
import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/cn";
import { Button, IconButton } from "@/components/ui/Button";
import { Popover } from "@/components/ui/Popover";

interface TaskDateRangePickerProps {
    startDate: string;
    dueDate: string;
    disabled?: boolean;
    onChange: (value: { startDate: string; dueDate: string }) => void;
}

const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

export function TaskDateRangePicker({
    startDate,
    dueDate,
    disabled = false,
    onChange,
}: TaskDateRangePickerProps) {
    const initialDate = parseIsoDate(startDate || dueDate) ?? new Date();
    const [visibleMonth, setVisibleMonth] = useState(
        new Date(initialDate.getFullYear(), initialDate.getMonth(), 1),
    );
    const [anchorDate, setAnchorDate] = useState<string | null>(null);
    const [hoverDate, setHoverDate] = useState<string | null>(null);
    const days = useMemo(() => calendarDays(visibleMonth), [visibleMonth]);
    const preview = previewRange(anchorDate, hoverDate, startDate, dueDate);

    function selectDate(date: string) {
        if (anchorDate === null) {
            setAnchorDate(date);
            setHoverDate(null);
            onChange({ startDate: date, dueDate: date });
            return;
        }
        const [start, end] = orderedRange(anchorDate, date);
        onChange({ startDate: start, dueDate: end });
        setAnchorDate(null);
        setHoverDate(null);
    }

    function moveFocus(event: KeyboardEvent<HTMLButtonElement>, date: string) {
        const offsets: Record<string, number> = {
            ArrowLeft: -1,
            ArrowRight: 1,
            ArrowUp: -7,
            ArrowDown: 7,
        };
        let next: Date | null = null;
        if (event.key in offsets) {
            next = addDays(parseIsoDate(date) as Date, offsets[event.key]);
        } else if (event.key === "Home") {
            next = addDays(parseIsoDate(date) as Date, -mondayIndex(parseIsoDate(date) as Date));
        } else if (event.key === "End") {
            next = addDays(
                parseIsoDate(date) as Date,
                6 - mondayIndex(parseIsoDate(date) as Date),
            );
        } else if (event.key === "PageUp" || event.key === "PageDown") {
            next = addMonths(parseIsoDate(date) as Date, event.key === "PageUp" ? -1 : 1);
        }
        if (next === null) return;
        event.preventDefault();
        setVisibleMonth(new Date(next.getFullYear(), next.getMonth(), 1));
        const nextIso = toIsoDate(next);
        window.requestAnimationFrame(() => {
            document.querySelector<HTMLButtonElement>(`[data-task-date="${nextIso}"]`)?.focus();
        });
    }

    return (
        <Popover
            label="Период задачи"
            align="start"
            width={336}
            disabled={disabled}
            triggerClassName="h-8 w-full justify-start px-3 font-normal"
            onOpenChange={(open) => {
                if (!open) {
                    setAnchorDate(null);
                    setHoverDate(null);
                } else {
                    const selected = parseIsoDate(startDate || dueDate) ?? new Date();
                    setVisibleMonth(new Date(selected.getFullYear(), selected.getMonth(), 1));
                }
            }}
            trigger={
                <>
                    <CalendarDays size={14} className="shrink-0 text-muted" aria-hidden="true" />
                    <span className={cn("truncate", !startDate && !dueDate && "text-muted")}>
                        {dateRangeLabel(startDate, dueDate)}
                    </span>
                </>
            }
        >
            {({ close }) => (
                <div className={cn("flex flex-col gap-3", disabled && "pointer-events-none opacity-50")}>
                    <div className="flex items-center justify-between gap-2">
                        <IconButton
                            label="Предыдущий месяц"
                            size="sm"
                            onClick={() => setVisibleMonth(addMonths(visibleMonth, -1))}
                        >
                            <ChevronLeft size={14} aria-hidden="true" />
                        </IconButton>
                        <p className="text-[13px] font-semibold capitalize text-primary">
                            {monthLabel(visibleMonth)}
                        </p>
                        <IconButton
                            label="Следующий месяц"
                            size="sm"
                            onClick={() => setVisibleMonth(addMonths(visibleMonth, 1))}
                        >
                            <ChevronRight size={14} aria-hidden="true" />
                        </IconButton>
                    </div>

                    <div role="grid" aria-label={monthLabel(visibleMonth)} className="grid grid-cols-7">
                        {WEEKDAYS.map((weekday) => (
                            <span
                                key={weekday}
                                role="columnheader"
                                className="flex h-7 items-center justify-center text-[10px] font-semibold text-disabled"
                            >
                                {weekday}
                            </span>
                        ))}
                        {days.map((day) => {
                            const iso = toIsoDate(day);
                            const outside = day.getMonth() !== visibleMonth.getMonth();
                            const selected = iso === preview.start || iso === preview.end;
                            const inRange = isWithin(iso, preview.start, preview.end);
                            const today = iso === toIsoDate(new Date());
                            return (
                                <button
                                    key={iso}
                                    type="button"
                                    role="gridcell"
                                    data-task-date={iso}
                                    tabIndex={
                                        iso === (anchorDate || startDate || toIsoDate(new Date()))
                                            ? 0
                                            : -1
                                    }
                                    aria-label={fullDateLabel(day)}
                                    aria-selected={selected}
                                    onClick={() => selectDate(iso)}
                                    onMouseEnter={() => anchorDate && setHoverDate(iso)}
                                    onFocus={() => anchorDate && setHoverDate(iso)}
                                    onKeyDown={(event) => moveFocus(event, iso)}
                                    className={cn(
                                        "relative flex h-11 items-center justify-center text-[12px] text-secondary outline-none sm:h-9",
                                        "transition-colors duration-[var(--duration-fast)] hover:bg-white/[0.055] focus-visible:z-10 focus-visible:ring-2 focus-visible:ring-accent/70",
                                        outside && "text-disabled/60",
                                        inRange && "bg-accent/[0.09]",
                                        selected && "bg-accent/85 font-semibold text-on-accent hover:bg-accent",
                                        today && !selected && "font-semibold text-accent",
                                        iso === anchorDate && "ring-1 ring-accent-border",
                                    )}
                                >
                                    {day.getDate()}
                                </button>
                            );
                        })}
                    </div>

                    <div className="grid grid-cols-2 gap-2 rounded-[var(--radius-control)] bg-white/[0.025] px-2.5 py-2">
                        <DateSummary label="Начало" value={startDate} />
                        <DateSummary label="Завершение" value={dueDate} />
                    </div>
                    <p aria-live="polite" className="min-h-4 text-[11px] text-muted">
                        {anchorDate
                            ? "Выберите дату завершения или нажмите «Готово» для одного дня."
                            : "Первая дата — начало, вторая — завершение."}
                    </p>
                    <div className="flex items-center justify-between gap-2">
                        <Button
                            size="sm"
                            variant="ghost"
                            disabled={!startDate && !dueDate}
                            onClick={() => {
                                onChange({ startDate: "", dueDate: "" });
                                setAnchorDate(null);
                                setHoverDate(null);
                            }}
                        >
                            Очистить
                        </Button>
                        <Button size="sm" variant="primary" onClick={close}>
                            Готово
                        </Button>
                    </div>
                </div>
            )}
        </Popover>
    );
}

function DateSummary({ label, value }: { label: string; value: string }) {
    return (
        <div className="min-w-0">
            <span className="block text-[9px] font-semibold tracking-[0.1em] text-disabled uppercase">
                {label}
            </span>
            <span className="block truncate text-[12px] text-secondary">
                {value ? singleDateLabel(value) : "Не задано"}
            </span>
        </div>
    );
}

function calendarDays(month: Date): Date[] {
    const first = new Date(month.getFullYear(), month.getMonth(), 1);
    const start = addDays(first, -mondayIndex(first));
    return Array.from({ length: 42 }, (_, index) => addDays(start, index));
}

function orderedRange(first: string, second: string): [string, string] {
    return first <= second ? [first, second] : [second, first];
}

function previewRange(
    anchor: string | null,
    hover: string | null,
    startDate: string,
    dueDate: string,
): { start: string; end: string } {
    if (anchor && hover) {
        const [start, end] = orderedRange(anchor, hover);
        return { start, end };
    }
    return { start: startDate, end: dueDate || startDate };
}

function isWithin(date: string, start: string, end: string): boolean {
    return Boolean(start && end && date >= start && date <= end);
}

function parseIsoDate(value: string): Date | null {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
    if (!match) return null;
    return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

function toIsoDate(value: Date): string {
    const year = value.getFullYear();
    const month = String(value.getMonth() + 1).padStart(2, "0");
    const day = String(value.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
}

function addDays(value: Date, amount: number): Date {
    return new Date(value.getFullYear(), value.getMonth(), value.getDate() + amount);
}

function addMonths(value: Date, amount: number): Date {
    return new Date(value.getFullYear(), value.getMonth() + amount, 1);
}

function mondayIndex(value: Date): number {
    return (value.getDay() + 6) % 7;
}

function monthLabel(value: Date): string {
    return new Intl.DateTimeFormat("ru-RU", { month: "long", year: "numeric" }).format(value);
}

function fullDateLabel(value: Date): string {
    return new Intl.DateTimeFormat("ru-RU", {
        weekday: "long",
        day: "numeric",
        month: "long",
        year: "numeric",
    }).format(value);
}

function singleDateLabel(value: string): string {
    const date = parseIsoDate(value);
    return date
        ? new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", year: "numeric" }).format(date)
        : value;
}

function dateRangeLabel(startDate: string, dueDate: string): string {
    if (!startDate && !dueDate) return "Выбрать даты";
    if (startDate && (!dueDate || startDate === dueDate)) return singleDateLabel(startDate);
    if (!startDate) return `До ${singleDateLabel(dueDate)}`;
    return `${singleDateLabel(startDate)} — ${singleDateLabel(dueDate)}`;
}
