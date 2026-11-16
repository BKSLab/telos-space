import { describe, expect, it } from "vitest";
import {
    buildMonthGrid,
    buildTimelineDays,
    calendarDaysBetween,
    calendarRange,
    moveTaskInterval,
    normalizeMonth,
    rescheduleCalendarTask,
    resizeTaskInterval,
    shiftMonth,
    shiftTimelineAnchor,
    taskInterval,
    tasksByDate,
    timelineBarGeometry,
    timelineDependencyGeometry,
    timelineRange,
} from "@/lib/calendar";
import { formatDateOnly, parseDateOnly } from "@/lib/dates";
import type { CalendarTask } from "@/lib/types";

describe("calendar date-only helpers", () => {
    it("builds six Monday-first weeks around a month", () => {
        const days = buildMonthGrid("2026-09", "2026-09-02");

        expect(days).toHaveLength(42);
        expect(days[0].date).toBe("2026-08-31");
        expect(days[6].date).toBe("2026-09-06");
        expect(days[2].isToday).toBe(true);
    });

    it("keeps leap day and rejects impossible date", () => {
        expect(formatDateOnly(parseDateOnly("2028-02-29") as Date)).toBe("2028-02-29");
        expect(parseDateOnly("2027-02-29")).toBeNull();
    });

    it("shifts over year boundary and normalizes invalid month", () => {
        expect(shiftMonth("2026-12", 1)).toBe("2027-01");
        expect(shiftMonth("2026-01", -1)).toBe("2025-12");
        expect(normalizeMonth("2026-15", new Date(2026, 8, 2))).toBe("2026-09");
    });

    it("returns the visible grid range", () => {
        expect(calendarRange("2026-09", "2026-09-02")).toEqual({
            dateFrom: "2026-08-31",
            dateTo: "2026-10-11",
        });
    });

    it("groups only scheduled tasks", () => {
        const base = {
            id: 1,
            key: "TEST-1",
            title: "Задача",
            stage_id: 1,
            wbs_node_id: null,
            priority: "HIGH",
            assignee: null,
            start_date: null,
            baseline_start_date: null,
            baseline_due_date: null,
            drift_days: null,
            is_done: false,
            is_overdue: false,
            is_due_soon: true,
            risk_level: "medium",
            risk_reasons: [],
            updated_at: "2026-09-02T00:00:00Z", revision: 1,
        } satisfies Omit<CalendarTask, "due_date">;
        const grouped = tasksByDate([
            { ...base, due_date: "2026-09-02" },
            { ...base, id: 2, due_date: "2026-09-02" },
            { ...base, id: 3, due_date: null },
        ]);

        expect(grouped.get("2026-09-02")).toHaveLength(2);
        expect(grouped.size).toBe(1);
    });

    it("recalculates explainable risks during an optimistic reschedule", () => {
        const task: CalendarTask = {
            id: 1,
            key: "TEST-1",
            title: "Задача",
            start_date: null,
            due_date: "2026-09-08",
            baseline_start_date: null,
            baseline_due_date: null,
            drift_days: null,
            stage_id: 1,
            wbs_node_id: null,
            priority: "HIGH",
            assignee: null,
            is_done: false,
            is_overdue: false,
            is_due_soon: true,
            risk_level: "medium",
            risk_reasons: [],
            updated_at: "2026-09-02T00:00:00Z", revision: 1,
        };

        const changed = rescheduleCalendarTask(task, "2026-09-01", "2026-09-02");

        expect(changed.is_overdue).toBe(true);
        expect(changed.risk_level).toBe("high");
        expect(changed.risk_reasons.map((reason) => reason.code)).toEqual([
            "OVERDUE",
            "NO_ASSIGNEE",
        ]);
    });

    it("builds week, month and quarter ranges without timezone shifts", () => {
        expect(timelineRange("2026-09-02", "week")).toEqual({
            dateFrom: "2026-08-31",
            dateTo: "2026-09-06",
        });
        expect(timelineRange("2026-09-02", "month")).toEqual({
            dateFrom: "2026-09-01",
            dateTo: "2026-09-30",
        });
        expect(timelineRange("2026-09-02", "quarter")).toEqual({
            dateFrom: "2026-07-01",
            dateTo: "2026-09-30",
        });
        expect(buildTimelineDays(timelineRange("2026-09-02", "week"))).toHaveLength(7);
        expect(calendarDaysBetween("2026-03-28", "2026-03-30")).toBe(2);
    });

    it("moves an interval preserving its duration and shifts period navigation", () => {
        const task: CalendarTask = {
            id: 1,
            key: "TEST-1",
            title: "Интервал",
            start_date: "2026-09-02",
            due_date: "2026-09-06",
            baseline_start_date: null,
            baseline_due_date: null,
            drift_days: null,
            stage_id: 1,
            wbs_node_id: null,
            priority: "MEDIUM",
            assignee: "Анна",
            is_done: false,
            is_overdue: false,
            is_due_soon: true,
            risk_level: "medium",
            risk_reasons: [],
            updated_at: "2026-09-02T00:00:00Z", revision: 1,
        };

        expect(taskInterval(task)?.durationDays).toBe(4);
        expect(moveTaskInterval(task, "2026-09-10")).toEqual({
            startDate: "2026-09-10",
            dueDate: "2026-09-14",
        });
        expect(shiftTimelineAnchor("2026-12-15", "quarter", 1)).toBe("2027-03-15");
    });

    it("resizes either interval edge and rejects an inverted result", () => {
        const task = {
            id: 1,
            key: "TEST-1",
            title: "Интервал",
            start_date: "2026-09-02",
            due_date: "2026-09-06",
            baseline_start_date: null,
            baseline_due_date: null,
            drift_days: null,
            stage_id: 1,
            wbs_node_id: null,
            priority: "MEDIUM",
            assignee: null,
            is_done: false,
            is_overdue: false,
            is_due_soon: true,
            risk_level: "medium",
            risk_reasons: [],
            updated_at: "2026-09-02T00:00:00Z", revision: 1,
        } satisfies CalendarTask;

        expect(resizeTaskInterval(task, "start", -2)).toEqual({
            startDate: "2026-08-31",
            dueDate: "2026-09-06",
        });
        expect(resizeTaskInterval(task, "end", 3)).toEqual({
            startDate: "2026-09-02",
            dueDate: "2026-09-09",
        });
        expect(resizeTaskInterval(task, "start", 5)).toBeNull();
        expect(
            timelineBarGeometry(
                task,
                { dateFrom: "2026-09-04", dateTo: "2026-09-10" },
                20,
            ),
        ).toEqual({
            left: 0,
            width: 60,
            clippedStart: true,
            clippedEnd: false,
            openEnd: false,
        });
    });

    it("shows a task with only a start date as an open interval", () => {
        const task = {
            id: 2,
            key: "TEST-2",
            title: "Открытый интервал",
            start_date: "2026-09-03",
            due_date: null,
            baseline_start_date: null,
            baseline_due_date: null,
            drift_days: null,
            stage_id: 1,
            wbs_node_id: null,
            priority: "MEDIUM",
            assignee: null,
            is_done: false,
            is_overdue: false,
            is_due_soon: false,
            risk_level: "medium",
            risk_reasons: [],
            updated_at: "2026-09-02T00:00:00Z", revision: 1,
        } satisfies CalendarTask;

        expect(tasksByDate([task]).get("2026-09-03")).toEqual([task]);
        expect(taskInterval(task)).toEqual({
            startDate: "2026-09-03",
            dueDate: "2026-09-03",
            durationDays: 0,
            openEnd: true,
        });
        expect(
            timelineBarGeometry(
                task,
                { dateFrom: "2026-09-01", dateTo: "2026-09-30" },
                20,
            ),
        ).toEqual({
            left: 40,
            width: 20,
            clippedStart: false,
            clippedEnd: false,
            openEnd: true,
        });
        expect(moveTaskInterval(task, "2026-09-10")).toEqual({
            startDate: "2026-09-10",
            dueDate: null,
        });
        expect(resizeTaskInterval(task, "start", 2)).toEqual({
            startDate: "2026-09-05",
            dueDate: null,
        });
    });

    it("builds an orthogonal dependency path in both timeline directions", () => {
        expect(timelineDependencyGeometry(40, 20, 100, 60).path).toBe(
            "M 40 20 H 70 V 60 H 100",
        );
        expect(timelineDependencyGeometry(100, 20, 40, 60).path).toBe(
            "M 100 20 H 114 V 60 H 40",
        );
    });
});
