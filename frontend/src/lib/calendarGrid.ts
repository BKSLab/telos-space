/**
 * Сетка календаря (День / Неделя / Месяц / Год) и состояние вида: чистые функции без React,
 * покрыты тестами (calendarGrid.test.ts). Раскладка многодневных задач по дорожкам,
 * «ещё N» в клетке месяца, тепловая шкала года, горячие клавиши, состояние в URL.
 */
import { formatDateOnly, parseDateOnly } from "@/lib/dates";
import { dataColor } from "@/lib/dataColor";
import {
    addDateDays,
    calendarDaysBetween,
    normalizeDate,
    normalizeScale,
    taskInterval,
    type DatedTask,
    type TimelineRange,
    type TimelineScale,
} from "@/lib/calendar";
import type { CalendarGridTask, CalendarStage } from "@/lib/types";

/** «Рядом» (split) — сетка сверху и шкала снизу, только на широком экране (docs/calendar-ui.md). */
export type CalendarViewMode = "grid" | "timeline" | "split";
export type GridScale = "day" | "week" | "month" | "year";
export type ColorMode = "status" | "stage" | "assignee" | "project";

export const GRID_SCALES: { value: GridScale; label: string; hotkey: string }[] = [
    { value: "day", label: "День", hotkey: "Д / D" },
    { value: "week", label: "Неделя", hotkey: "Н / W" },
    { value: "month", label: "Месяц", hotkey: "М / M" },
    { value: "year", label: "Год", hotkey: "Г / Y" },
];

export const TIMELINE_SCALES: { value: TimelineScale; label: string; hotkey: string }[] = [
    { value: "week", label: "Неделя", hotkey: "Н / W" },
    { value: "month", label: "Месяц", hotkey: "М / M" },
    { value: "quarter", label: "Квартал", hotkey: "" },
    { value: "year", label: "Год", hotkey: "Г / Y" },
];

export const COLOR_MODE_LABELS: Record<ColorMode, string> = {
    status: "статусу",
    stage: "этапу",
    assignee: "исполнителю",
    project: "проекту",
};

// ---------------------------------------------------------------- состояние вида

export interface CalendarViewState {
    view: CalendarViewMode;
    gridScale: GridScale;
    timelineScale: TimelineScale;
    date: string;
    /** «Уместить проект»: шкала показывает весь срок проекта вместо периода вокруг даты. */
    fit: boolean;
}

export interface CalendarViewDefaults {
    view: CalendarViewMode;
    gridScale: GridScale;
    timelineScale: TimelineScale;
}

/** Сохранённый выбор вида (localStorage): это настройка отображения, не данные. */
export interface StoredCalendarView {
    view?: string;
    gridScale?: string;
    timelineScale?: string;
}

export function normalizeView(value: string | null | undefined): CalendarViewMode | null {
    return value === "grid" || value === "timeline" || value === "split" ? value : null;
}

export function normalizeGridScale(value: string | null | undefined): GridScale | null {
    if (value === "day" || value === "week" || value === "month" || value === "year") return value;
    if (value === "quarter") return "month";
    return null;
}

export function normalizeTimelineScale(value: string | null | undefined): TimelineScale | null {
    if (value === "day") return "week";
    if (value === "week" || value === "month" || value === "quarter" || value === "year") {
        return normalizeScale(value);
    }
    return null;
}

/**
 * Вид из URL (?view=&scale=&date=), иначе — последний выбор пользователя, иначе умолчание.
 * Вид и дата берутся из канонических параметров view и date.
 */
export function readCalendarViewState(
    params: URLSearchParams,
    stored: StoredCalendarView | null,
    defaults: CalendarViewDefaults,
    today: string,
): CalendarViewState {
    const view =
        normalizeView(params.get("view")) ??
        normalizeView(stored?.view) ??
        defaults.view;
    const urlScale = params.get("scale");
    // В «Рядом» scale — масштаб сетки (только неделя или месяц), масштаб шкалы — в tscale.
    const splitGridScale = (value: string | null | undefined) => {
        const scale = normalizeGridScale(value);
        return scale === "day" ? "week" : scale === "year" ? "month" : scale;
    };
    const gridScale =
        (view === "grid" ? normalizeGridScale(urlScale) : view === "split" ? splitGridScale(urlScale) : null) ??
        normalizeGridScale(stored?.gridScale) ??
        defaults.gridScale;
    const timelineScale =
        (view === "timeline" ? normalizeTimelineScale(urlScale) : view === "split" ? normalizeTimelineScale(params.get("tscale")) : null) ??
        normalizeTimelineScale(stored?.timelineScale) ??
        defaults.timelineScale;
    const date = normalizeDate(params.get("date"), today);
    return { view, gridScale, timelineScale, date, fit: view !== "grid" && params.get("fit") === "1" };
}

/** Новые параметры адреса с изменённым видом; прочие параметры (фильтры) не трогаются. */
export function writeCalendarViewParams(
    params: URLSearchParams,
    state: Pick<CalendarViewState, "view" | "date"> & { scale: string; tscale?: string; fit?: boolean },
): URLSearchParams {
    const next = new URLSearchParams(params);
    next.set("view", state.view);
    next.set("scale", state.scale);
    next.set("date", state.date);
    if (state.view === "split" && state.tscale) next.set("tscale", state.tscale);
    else next.delete("tscale");
    if (state.fit && state.view !== "grid") next.set("fit", "1");
    else next.delete("fit");
    return next;
}

// ---------------------------------------------------------------- периоды

export function mondayOf(date: string): string {
    const parsed = parseDateOnly(date) ?? new Date();
    const offset = (parsed.getDay() + 6) % 7;
    return formatDateOnly(new Date(parsed.getFullYear(), parsed.getMonth(), parsed.getDate() - offset));
}

/** Номер недели по ISO 8601 (неделя с понедельника, первая — с первым четвергом года). */
export function isoWeekNumber(date: string): number {
    const parsed = parseDateOnly(date);
    if (parsed === null) return 0;
    const utc = new Date(Date.UTC(parsed.getFullYear(), parsed.getMonth(), parsed.getDate()));
    const dayNumber = (utc.getUTCDay() + 6) % 7;
    utc.setUTCDate(utc.getUTCDate() - dayNumber + 3);
    const firstThursday = new Date(Date.UTC(utc.getUTCFullYear(), 0, 4));
    return 1 + Math.round(((utc.getTime() - firstThursday.getTime()) / 86_400_000 - 3 + ((firstThursday.getUTCDay() + 6) % 7)) / 7);
}

/** Видимый диапазон сетки: месяц — полные 6 недель (42 дня) с понедельника. */
export function gridRange(scale: GridScale, date: string): TimelineRange {
    const parsed = parseDateOnly(date) ?? new Date();
    if (scale === "day") return { dateFrom: date, dateTo: date };
    if (scale === "week") {
        const from = mondayOf(date);
        return { dateFrom: from, dateTo: addDateDays(from, 6) };
    }
    if (scale === "year") {
        return { dateFrom: `${parsed.getFullYear()}-01-01`, dateTo: `${parsed.getFullYear()}-12-31` };
    }
    const first = formatDateOnly(new Date(parsed.getFullYear(), parsed.getMonth(), 1));
    const from = mondayOf(first);
    return { dateFrom: from, dateTo: addDateDays(from, 41) };
}

export function shiftGridDate(scale: GridScale, date: string, delta: number): string {
    if (scale === "day") return addDateDays(date, delta);
    if (scale === "week") return addDateDays(date, delta * 7);
    const parsed = parseDateOnly(date) ?? new Date();
    const months = scale === "year" ? 12 : 1;
    return formatDateOnly(new Date(parsed.getFullYear(), parsed.getMonth() + delta * months, 1));
}

const MONTH_YEAR = new Intl.DateTimeFormat("ru-RU", { month: "long", year: "numeric" });
const DAY_FULL = new Intl.DateTimeFormat("ru-RU", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
const DAY_SHORT = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short" });

function capitalize(value: string): string {
    return value.charAt(0).toUpperCase() + value.slice(1);
}

export function gridTitle(scale: GridScale, date: string): string {
    const parsed = parseDateOnly(date) ?? new Date();
    if (scale === "day") return capitalize(DAY_FULL.format(parsed));
    if (scale === "year") return `${parsed.getFullYear()} год`;
    if (scale === "month") return capitalize(MONTH_YEAR.format(parsed));
    const from = mondayOf(date);
    const to = addDateDays(from, 6);
    const fromDate = parseDateOnly(from) ?? parsed;
    const toDate = parseDateOnly(to) ?? parsed;
    return `${DAY_SHORT.format(fromDate)} — ${DAY_SHORT.format(toDate)} ${toDate.getFullYear()} · ${isoWeekNumber(from)} нед.`;
}

/** Дни видимого периода подряд. */
export function rangeDates(range: TimelineRange): string[] {
    const count = calendarDaysBetween(range.dateFrom, range.dateTo) + 1;
    return Array.from({ length: Math.max(count, 0) }, (_, index) => addDateDays(range.dateFrom, index));
}

// ---------------------------------------------------------------- раскладка задач

export interface TaskSpan {
    start: string;
    end: string;
}

/**
 * Интервал задачи для сетки. Задача без срока, но с началом, — точка в день начала
 * (открытый конец в сетке не рисуем полосой в бесконечность).
 */
export function taskSpan(task: DatedTask): TaskSpan | null {
    const interval = taskInterval(task);
    if (interval === null) return null;
    if (interval.openEnd) return { start: interval.startDate, end: interval.startDate };
    return { start: interval.startDate, end: interval.dueDate };
}

export function isMultiDay(task: DatedTask): boolean {
    const span = taskSpan(task);
    return span !== null && span.start < span.end;
}

/** День, в который однодневная задача стоит в сетке: срок, иначе начало. */
export function singleDayDate(task: DatedTask): string | null {
    return task.due_date ?? task.start_date;
}

export function coversDate(task: DatedTask, date: string): boolean {
    const span = taskSpan(task);
    return span !== null && span.start <= date && date <= span.end;
}

export interface LaneSegment<T> {
    item: T;
    /** Колонка начала отрезка в пределах строки (0 — понедельник). */
    startCol: number;
    span: number;
    lane: number;
    /** Задача началась раньше строки / продолжается дальше неё. */
    clippedStart: boolean;
    clippedEnd: boolean;
}

/**
 * Многодневные задачи строки (неделя из `days` дней с `rowFrom`) по дорожкам без наложения:
 * раньше начавшиеся и более длинные — выше; каждая берёт первую свободную дорожку.
 */
export function layoutLanes<T extends DatedTask & { key?: string }>(
    tasks: T[],
    rowFrom: string,
    days = 7,
): { segments: LaneSegment<T>[]; laneCount: number } {
    const rowTo = addDateDays(rowFrom, days - 1);
    const candidates = tasks
        .map((item) => ({ item, span: taskSpan(item) }))
        .filter(
            (entry): entry is { item: T; span: TaskSpan } =>
                entry.span !== null &&
                entry.span.start < entry.span.end &&
                entry.span.end >= rowFrom &&
                entry.span.start <= rowTo,
        )
        .sort(
            (first, second) =>
                first.span.start.localeCompare(second.span.start) ||
                second.span.end.localeCompare(first.span.end) ||
                (first.item.key ?? "").localeCompare(second.item.key ?? ""),
        );
    const laneEnds: number[] = [];
    const segments = candidates.map(({ item, span }) => {
        const visibleStart = span.start < rowFrom ? rowFrom : span.start;
        const visibleEnd = span.end > rowTo ? rowTo : span.end;
        const startCol = calendarDaysBetween(rowFrom, visibleStart);
        const endCol = calendarDaysBetween(rowFrom, visibleEnd);
        let lane = laneEnds.findIndex((end) => end < startCol);
        if (lane === -1) {
            lane = laneEnds.length;
            laneEnds.push(endCol);
        } else {
            laneEnds[lane] = endCol;
        }
        return {
            item,
            startCol,
            span: endCol - startCol + 1,
            lane,
            clippedStart: span.start < rowFrom,
            clippedEnd: span.end > rowTo,
        };
    });
    return { segments, laneCount: laneEnds.length };
}

/** Сколько дорожек полос показывать в строке месяца: одна строка клетки всегда под «ещё N». */
export function visibleBarRows(laneCount: number, maxRows: number): number {
    return Math.max(0, Math.min(laneCount, maxRows - 1));
}

/**
 * Что помещается в клетке месяца: полосы в дорожках < barRows рисуются через клетки,
 * ниже — однодневные элементы. Если что-то не влезло, последняя строка — «ещё N».
 */
export function monthCellLayout(options: {
    barLanes: number[];
    barRows: number;
    singles: number;
    maxRows: number;
}): { shownSingles: number; hidden: number } {
    const hiddenBars = options.barLanes.filter((lane) => lane >= options.barRows).length;
    const slots = Math.max(0, options.maxRows - options.barRows);
    if (hiddenBars === 0 && options.singles <= slots) {
        return { shownSingles: options.singles, hidden: 0 };
    }
    const shownSingles = Math.min(options.singles, Math.max(0, slots - 1));
    return { shownSingles, hidden: hiddenBars + options.singles - shownSingles };
}

/**
 * Перенос в сетке: карточку взяли за день `grabDate` и бросили на `targetDate` — весь
 * интервал сдвигается на ту же разницу (у однодневной задачи это просто новый срок).
 * null — переносить нечего (тот же день или у задачи нет дат).
 */
export function shiftTaskDates(
    task: DatedTask,
    grabDate: string,
    targetDate: string,
): { startDate: string | null; dueDate: string | null } | null {
    const delta = calendarDaysBetween(grabDate, targetDate);
    if (delta === 0 || (task.start_date === null && task.due_date === null)) return null;
    return {
        startDate: task.start_date === null ? null : addDateDays(task.start_date, delta),
        dueDate: task.due_date === null ? null : addDateDays(task.due_date, delta),
    };
}

// ---------------------------------------------------------------- год

/** Сколько сроков приходится на каждый день года. */
export function yearDueCounts(tasks: DatedTask[], year: number): Map<string, number> {
    const counts = new Map<string, number>();
    const prefix = `${year}-`;
    for (const task of tasks) {
        if (task.due_date === null || !task.due_date.startsWith(prefix)) continue;
        counts.set(task.due_date, (counts.get(task.due_date) ?? 0) + 1);
    }
    return counts;
}

/** Ступень тепловой шкалы: 0 — сроков нет, 1 — один, 2 — два, 3 — три-четыре, 4 — пять и больше. */
export function heatLevel(count: number): 0 | 1 | 2 | 3 | 4 {
    if (count <= 0) return 0;
    if (count === 1) return 1;
    if (count === 2) return 2;
    if (count <= 4) return 3;
    return 4;
}

export const HEAT_LEGEND: { level: 1 | 2 | 3 | 4; label: string }[] = [
    { level: 1, label: "1" },
    { level: 2, label: "2" },
    { level: 3, label: "3–4" },
    { level: 4, label: "5+" },
];

/** Двенадцать мини-месяцев года: по 6 недель с понедельника, как в месяце. */
export function yearMonths(year: number): { month: number; first: string; days: string[] }[] {
    return Array.from({ length: 12 }, (_, month) => {
        const first = formatDateOnly(new Date(year, month, 1));
        const from = mondayOf(first);
        return { month, first, days: Array.from({ length: 42 }, (_, index) => addDateDays(from, index)) };
    });
}

// ---------------------------------------------------------------- цвет

const PALETTE = ["var(--color-accent)", "var(--color-success)", "var(--color-warning)", "var(--color-purple)", "var(--color-info)", "var(--color-ai-cyan)"];

function hashIndex(value: string, size: number): number {
    let hash = 0;
    for (const char of value) hash = (hash * 31 + char.charCodeAt(0)) | 0;
    return Math.abs(hash) % size;
}

/** Цвет-маркер задачи (левая полоса карточки) по выбранному признаку. */
export function taskColor(
    task: CalendarGridTask,
    mode: ColorMode,
    stagesById?: ReadonlyMap<number, Pick<CalendarStage, "color">>,
): string {
    if (mode === "stage") {
        const stage = task.stage_id === null ? undefined : stagesById?.get(task.stage_id);
        return dataColor(stage?.color) ?? "var(--color-text-muted)";
    }
    if (mode === "assignee") {
        return task.assignee ? PALETTE[hashIndex(task.assignee, PALETTE.length)] : "var(--color-text-disabled)";
    }
    if (mode === "project") {
        const source = task.source;
        if (source?.color) return source.color;
        return source ? PALETTE[hashIndex(`${source.kind}:${source.id ?? ""}`, PALETTE.length)] : "var(--color-text-muted)";
    }
    if (task.is_done) return "var(--color-success)";
    if (task.is_overdue) return "var(--color-danger)";
    if (task.is_due_soon) return "var(--color-warning)";
    const state = task.status ?? task.state;
    if (state === "REVIEW") return "var(--color-ai-violet)";
    if (state === "NEW") return "var(--color-text-muted)";
    return "var(--color-info)";
}

// ---------------------------------------------------------------- горячие клавиши

export type CalendarHotkey = GridScale | "prev" | "next" | "today";

const HOTKEYS: Record<string, CalendarHotkey> = {
    д: "day",
    d: "day",
    н: "week",
    w: "week",
    м: "month",
    m: "month",
    г: "year",
    y: "year",
    t: "today",
    е: "today",
    arrowleft: "prev",
    arrowright: "next",
};

/** Фокус в поле ввода — горячие клавиши календаря молчат. */
export function isTypingTarget(target: EventTarget | null): boolean {
    if (typeof Element === "undefined" || !(target instanceof Element)) return false;
    if (target.closest("input, textarea, select, [contenteditable=''], [contenteditable='true'], [role='dialog'], [role='textbox']")) {
        return true;
    }
    return false;
}

export function calendarHotkey(event: {
    key: string;
    ctrlKey?: boolean;
    metaKey?: boolean;
    altKey?: boolean;
    target: EventTarget | null;
}): CalendarHotkey | null {
    if (event.ctrlKey || event.metaKey || event.altKey) return null;
    if (isTypingTarget(event.target)) return null;
    const action = HOTKEYS[event.key.toLowerCase()] ?? null;
    // Стрелки заняты у ручек растягивания полосы и у перетаскивания с клавиатуры.
    if (
        (action === "prev" || action === "next") &&
        typeof Element !== "undefined" &&
        event.target instanceof Element &&
        event.target.closest("[data-own-arrows], [aria-roledescription='draggable']")
    ) {
        return null;
    }
    return action;
}
