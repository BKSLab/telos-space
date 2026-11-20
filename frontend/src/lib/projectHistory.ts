import { PRIORITY_LABELS, type TaskPriority, type TaskStatus } from "./types";
import { TASK_STATUS_LABELS } from "./tasks";

export function historyDayKey(date: Date, timezone: string): string {
    const parts = new Intl.DateTimeFormat("en", { timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(date);
    return ["year", "month", "day"].map(type => parts.find(part => part.type === type)!.value).join("-");
}

export function historyPeriod(days: number, timezone: string, now = new Date()) {
    const until = historyDayKey(now, timezone);
    // Calendar arithmetic avoids 23/25-hour days at DST transitions.
    const start = new Date(`${until}T12:00:00Z`);
    start.setUTCDate(start.getUTCDate() - days + 1);
    return { since: start.toISOString().slice(0, 10), until };
}

export function historyValue(value: string | null | undefined, eventType?: string | null): string {
    if (!value) return "Не задано";
    if (eventType === "PRIORITY_CHANGED") return PRIORITY_LABELS[value as TaskPriority] ?? value;
    if (eventType === "STAGE_CHANGED") return TASK_STATUS_LABELS[value as TaskStatus] ?? value;
    if (["DUE_DATE_CHANGED", "START_DATE_CHANGED", "DATES_CHANGED", "BASELINE_CHANGED"].includes(eventType ?? "") || /^\d{4}-\d{2}-\d{2}$/.test(value)) {
        return value.replace(/\b(\d{4})-(\d{2})-(\d{2})\b/g, "$3.$2.$1");
    }
    return value;
}
