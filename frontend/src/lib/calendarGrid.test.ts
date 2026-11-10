// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { dataColor } from "./dataColor";
import { buildMonthGrid, buildTimelineDays, dayOffInfo, shiftTimelineAnchor, timelineRange, timelineTitle } from "@/lib/calendar";
import {
    calendarHotkey,
    gridRange,
    gridTitle,
    heatLevel,
    isoWeekNumber,
    layoutLanes,
    monthCellLayout,
    readCalendarViewState,
    shiftGridDate,
    shiftTaskDates,
    taskColor,
    visibleBarRows,
    writeCalendarViewParams,
    yearDueCounts,
    yearMonths,
} from "@/lib/calendarGrid";
import type { CalendarGridTask, CalendarHoliday } from "@/lib/types";

function task(key: string, start: string | null, due: string | null, extra: Partial<CalendarGridTask> = {}): CalendarGridTask {
    return {
        id: Number(key.replace(/\D/g, "")) || 1,
        key,
        title: key,
        start_date: start,
        due_date: due,
        baseline_start_date: null,
        baseline_due_date: null,
        drift_days: null,
        wbs_node_id: null,
        stage_id: 1,
        priority: "MEDIUM",
        assignee: null,
        is_done: false,
        is_overdue: false,
        is_due_soon: false,
        risk_level: null,
        risk_reasons: [],
        updated_at: "2026-09-01T00:00:00Z",
        ...extra,
    };
}

const DEFAULTS = { view: "grid", gridScale: "month", timelineScale: "month" } as const;

describe("раскладка многодневных задач по дорожкам", () => {
    const monday = "2026-09-28";

    it("не пересекающиеся задачи делят одну дорожку, пересекающиеся — разные", () => {
        const { segments, laneCount } = layoutLanes(
            [
                task("A-1", "2026-09-28", "2026-09-29"),
                task("A-2", "2026-09-30", "2026-10-02"),
                task("A-3", "2026-09-29", "2026-10-01"),
            ],
            monday,
        );
        expect(laneCount).toBe(2);
        const byKey = Object.fromEntries(segments.map((segment) => [segment.item.key, segment]));
        expect(byKey["A-1"]).toMatchObject({ lane: 0, startCol: 0, span: 2 });
        expect(byKey["A-3"]).toMatchObject({ lane: 1, startCol: 1, span: 3 });
        expect(byKey["A-2"]).toMatchObject({ lane: 0, startCol: 2, span: 3 });
    });

    it("обрезает задачу по границам недели и помечает продолжение", () => {
        const { segments } = layoutLanes([task("B-1", "2026-09-20", "2026-10-08")], monday);
        expect(segments[0]).toMatchObject({ startCol: 0, span: 7, clippedStart: true, clippedEnd: true });
    });

    it("однодневные и задачи вне недели в дорожки не попадают", () => {
        const { segments, laneCount } = layoutLanes(
            [task("C-1", null, "2026-09-29"), task("C-2", "2026-09-30", "2026-09-30"), task("C-3", "2026-10-10", "2026-10-12")],
            monday,
        );
        expect(segments).toHaveLength(0);
        expect(laneCount).toBe(0);
    });

    it("задача с началом без срока — однодневная, а не бесконечная полоса", () => {
        const { segments } = layoutLanes([task("D-1", "2026-09-29", null)], monday);
        expect(segments).toHaveLength(0);
    });

    it("длинная задача встаёт выше короткой с тем же началом", () => {
        const { segments } = layoutLanes([task("E-1", "2026-09-28", "2026-09-29"), task("E-2", "2026-09-28", "2026-10-03")], monday);
        expect(segments.find((segment) => segment.item.key === "E-2")?.lane).toBe(0);
    });
});

describe("клетка месяца и «ещё N»", () => {
    it("всё помещается — «ещё» нет", () => {
        expect(monthCellLayout({ barLanes: [0], barRows: 1, singles: 2, maxRows: 4 })).toEqual({ shownSingles: 2, hidden: 0 });
    });

    it("не помещается — последняя строка уходит под «ещё N»", () => {
        expect(monthCellLayout({ barLanes: [], barRows: 0, singles: 6, maxRows: 4 })).toEqual({ shownSingles: 3, hidden: 3 });
    });

    it("полосы в скрытых дорожках считаются в «ещё N»", () => {
        expect(visibleBarRows(5, 4)).toBe(3);
        expect(monthCellLayout({ barLanes: [0, 1, 2, 3, 4], barRows: 3, singles: 1, maxRows: 4 })).toEqual({ shownSingles: 0, hidden: 3 });
    });
});

describe("год: тепловая шкала сроков", () => {
    it("считает сроки по дням только своего года", () => {
        const counts = yearDueCounts(
            [task("F-1", null, "2026-03-01"), task("F-2", "2026-02-01", "2026-03-01"), task("F-3", null, "2027-03-01"), task("F-4", "2026-03-01", null)],
            2026,
        );
        expect(counts.get("2026-03-01")).toBe(2);
        expect(counts.size).toBe(1);
    });

    it("ступени шкалы", () => {
        expect([0, 1, 2, 3, 4, 5, 12].map(heatLevel)).toEqual([0, 1, 2, 3, 3, 4, 4]);
    });

    it("двенадцать мини-месяцев по 42 дня с понедельника", () => {
        const months = yearMonths(2026);
        expect(months).toHaveLength(12);
        expect(months[0].days[0]).toBe("2025-12-29");
        expect(months[8].first).toBe("2026-09-01");
        expect(months.every((month) => month.days.length === 42)).toBe(true);
    });
});

describe("праздники и выходные", () => {
    const holidays = new Map<string, CalendarHoliday>([
        ["2026-11-04", { date: "2026-11-04", kind: "HOLIDAY", title: "День народного единства" }],
        ["2026-11-07", { date: "2026-11-07", kind: "WORKING_DAY", title: null }],
        ["2026-11-03", { date: "2026-11-03", kind: "SHORT_DAY", title: null }],
    ]);

    it("праздник в будни — выходной с названием", () => {
        expect(dayOffInfo("2026-11-04", holidays)).toEqual({ isOff: true, title: "День народного единства" });
    });

    it("рабочая суббота — как будний день", () => {
        expect(dayOffInfo("2026-11-07", holidays).isOff).toBe(false);
        expect(dayOffInfo("2026-11-08", holidays).isOff).toBe(true);
    });

    it("сокращённый день — рабочий с подсказкой", () => {
        expect(dayOffInfo("2026-11-03", holidays)).toEqual({ isOff: false, title: "Сокращённый день" });
    });

    it("без календаря — только сб/вс", () => {
        const days = buildMonthGrid("2026-11", "2026-11-01");
        expect(days.find((day) => day.date === "2026-11-04")?.isOff).toBe(false);
        expect(buildTimelineDays({ dateFrom: "2026-11-04", dateTo: "2026-11-04" }, "2026-11-01", holidays)[0].isOff).toBe(true);
    });
});

describe("периоды сетки и шкалы", () => {
    it("месяц — 6 недель с понедельника, неделя — пн–вс, год — весь год", () => {
        expect(gridRange("month", "2026-09-15")).toEqual({ dateFrom: "2026-08-31", dateTo: "2026-10-11" });
        expect(gridRange("week", "2026-10-01")).toEqual({ dateFrom: "2026-09-28", dateTo: "2026-10-04" });
        expect(gridRange("year", "2026-10-01")).toEqual({ dateFrom: "2026-01-01", dateTo: "2026-12-31" });
        expect(gridRange("day", "2026-10-01")).toEqual({ dateFrom: "2026-10-01", dateTo: "2026-10-01" });
    });

    it("листание по периодам", () => {
        expect(shiftGridDate("day", "2026-12-31", 1)).toBe("2027-01-01");
        expect(shiftGridDate("week", "2026-10-01", -1)).toBe("2026-09-24");
        expect(shiftGridDate("month", "2026-01-31", 1)).toBe("2026-02-01");
        expect(shiftGridDate("year", "2026-05-10", 1)).toBe("2027-05-01");
    });

    it("номер недели по ISO", () => {
        expect(isoWeekNumber("2026-01-01")).toBe(1);
        expect(isoWeekNumber("2026-09-28")).toBe(40);
        expect(isoWeekNumber("2027-01-01")).toBe(53);
    });

    it("заголовки", () => {
        expect(gridTitle("year", "2026-03-01")).toBe("2026 год");
        expect(gridTitle("month", "2026-09-10")).toBe("Сентябрь 2026 г.");
        expect(gridTitle("week", "2026-10-01")).toContain("40 нед.");
    });

    it("шкала: год целиком и сдвиг месяца без перескока через февраль", () => {
        expect(timelineRange("2026-06-10", "year")).toEqual({ dateFrom: "2026-01-01", dateTo: "2026-12-31" });
        expect(timelineTitle({ dateFrom: "2026-01-01", dateTo: "2026-12-31" }, "year")).toBe("2026 год");
        expect(shiftTimelineAnchor("2026-01-31", "month", 1)).toBe("2026-02-28");
        expect(shiftTimelineAnchor("2026-06-10", "year", -1)).toBe("2025-06-10");
    });
});

describe("состояние вида в адресе", () => {
    it("адрес главнее сохранённого выбора", () => {
        const state = readCalendarViewState(
            new URLSearchParams("view=timeline&scale=quarter&date=2026-05-02"),
            { view: "grid", gridScale: "week" },
            DEFAULTS,
            "2026-09-28",
        );
        expect(state).toEqual({ view: "timeline", gridScale: "week", timelineScale: "quarter", date: "2026-05-02", fit: false });
    });

    it("без адреса — последний выбор, без него — умолчание", () => {
        expect(readCalendarViewState(new URLSearchParams(), { view: "grid", gridScale: "year" }, DEFAULTS, "2026-09-28")).toMatchObject({
            view: "grid",
            gridScale: "year",
            date: "2026-09-28",
        });
        expect(readCalendarViewState(new URLSearchParams(), null, DEFAULTS, "2026-09-28").view).toBe("grid");
    });

    it("неизвестный параметр не меняет вид и дату календаря", () => {
        const state = readCalendarViewState(new URLSearchParams("anchor=2026-10-14&milestone=1"), { view: "grid" }, DEFAULTS, "2026-09-28");
        expect(state).toMatchObject({ view: "grid", date: "2026-09-28" });
    });

    it("масштаб, которого нет у вида, заменяется ближайшим", () => {
        expect(readCalendarViewState(new URLSearchParams("view=grid&scale=quarter"), null, DEFAULTS, "2026-09-28").gridScale).toBe("month");
        expect(readCalendarViewState(new URLSearchParams("view=timeline&scale=day"), null, DEFAULTS, "2026-09-28").timelineScale).toBe("week");
        expect(readCalendarViewState(new URLSearchParams("date=не-дата"), null, DEFAULTS, "2026-09-28").date).toBe("2026-09-28");
    });

    it("«Рядом»: scale — сетка (неделя/месяц), tscale — шкала; fit только не у сетки", () => {
        const state = readCalendarViewState(new URLSearchParams("view=split&scale=year&tscale=quarter&fit=1"), null, DEFAULTS, "2026-09-28");
        expect(state).toMatchObject({ view: "split", gridScale: "month", timelineScale: "quarter", fit: true });
        expect(readCalendarViewState(new URLSearchParams("view=split&scale=day"), null, DEFAULTS, "2026-09-28").gridScale).toBe("week");
        expect(readCalendarViewState(new URLSearchParams("view=grid&fit=1"), null, DEFAULTS, "2026-09-28").fit).toBe(false);
        const next = writeCalendarViewParams(new URLSearchParams("fit=1&tscale=year"), { view: "split", scale: "month", tscale: "week", date: "2026-09-28", fit: false });
        expect(next.toString()).toBe("tscale=week&view=split&scale=month&date=2026-09-28");
        const grid = writeCalendarViewParams(new URLSearchParams("fit=1&tscale=year"), { view: "grid", scale: "month", tscale: "week", date: "2026-09-28", fit: true });
        expect(grid.toString()).toBe("view=grid&scale=month&date=2026-09-28");
    });

    it("запись вида сохраняет фильтры", () => {
        const next = writeCalendarViewParams(new URLSearchParams("priority=HIGH"), { view: "grid", scale: "week", date: "2026-09-28" });
        expect(next.toString()).toBe("priority=HIGH&view=grid&scale=week&date=2026-09-28");
    });
});

describe("горячие клавиши", () => {
    const body = typeof document === "undefined" ? null : document.body;

    it("русская и латинская раскладки", () => {
        expect(calendarHotkey({ key: "Д", target: body })).toBe("day");
        expect(calendarHotkey({ key: "w", target: body })).toBe("week");
        expect(calendarHotkey({ key: "м", target: body })).toBe("month");
        expect(calendarHotkey({ key: "Y", target: body })).toBe("year");
        expect(calendarHotkey({ key: "е", target: body })).toBe("today");
        expect(calendarHotkey({ key: "t", target: body })).toBe("today");
        expect(calendarHotkey({ key: "ArrowLeft", target: body })).toBe("prev");
        expect(calendarHotkey({ key: "ArrowRight", target: body })).toBe("next");
        expect(calendarHotkey({ key: "x", target: body })).toBeNull();
    });

    it("молчат в полях ввода и с модификаторами", () => {
        const input = document.createElement("input");
        document.body.append(input);
        expect(calendarHotkey({ key: "m", target: input })).toBeNull();
        expect(calendarHotkey({ key: "m", ctrlKey: true, target: body })).toBeNull();
        const handle = document.createElement("button");
        handle.setAttribute("data-own-arrows", "");
        document.body.append(handle);
        expect(calendarHotkey({ key: "ArrowLeft", target: handle })).toBeNull();
        expect(calendarHotkey({ key: "m", target: handle })).toBe("month");
        input.remove();
        handle.remove();
    });
});

describe("цвет задачи", () => {
    it("по статусу: просрочка и выполнение", () => {
        expect(taskColor(task("G-1", null, "2026-01-01", { is_overdue: true }), "status")).toBe("var(--color-danger)");
        expect(taskColor(task("G-2", null, "2026-01-01", { is_done: true }), "status")).toBe("var(--color-success)");
    });

    it("по этапу и проекту — цвет из данных, по исполнителю — стабильный", () => {
        expect(taskColor(task("G-3", null, null), "stage", new Map([[1, { color: "#123456" }]]))).toBe(dataColor("#123456"));
        const withSource = task("G-4", null, null, { source: { kind: "PROJECT", id: 1, key: "DEMO", name: "Демо", color: "#7C1623" } });
        expect(taskColor(withSource, "project")).toBe(dataColor("#7C1623"));
        const first = taskColor(task("G-5", null, null, { assignee: "Иванова Анна" }), "assignee");
        expect(taskColor(task("G-6", null, null, { assignee: "Иванова Анна" }), "assignee")).toBe(first);
    });
});

describe("перенос перетаскиванием в сетке", () => {
    it("однодневная — новый срок", () => {
        expect(shiftTaskDates(task("H-1", null, "2026-10-01"), "2026-10-01", "2026-10-05")).toEqual({ startDate: null, dueDate: "2026-10-05" });
    });

    it("многодневная, взятая за середину, — сдвиг интервала на ту же разницу", () => {
        expect(shiftTaskDates(task("H-2", "2026-10-01", "2026-10-04"), "2026-10-03", "2026-10-06")).toEqual({ startDate: "2026-10-04", dueDate: "2026-10-07" });
    });

    it("тот же день — ничего не меняется", () => {
        expect(shiftTaskDates(task("H-3", "2026-10-01", "2026-10-04"), "2026-10-02", "2026-10-02")).toBeNull();
    });
});
