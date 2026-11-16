import { describe, expect, it } from "vitest";
import {
    assigneeInitials,
    clampLabelWidth,
    clampSplitRatio,
    exportFileName,
    fitTimelineScale,
    ganttCellValue,
    ganttColumnsWidth,
    mergeProjectCalendars,
    nextTimelineScale,
    normalizeGanttColumns,
    pdfPageSize,
    projectSpan,
    rangeDays,
    stretchDayWidth,
    splitRange,
    timelineLegend,
    unionRange,
} from "@/lib/ganttView";
import type { CalendarStage, CalendarTask, ProjectCalendar } from "@/lib/types";

function task(overrides: Partial<CalendarTask>): CalendarTask {
    return {
        id: 1,
        key: "DEMO-1",
        title: "Задача",
        start_date: "2026-09-01",
        due_date: "2026-09-10",
        baseline_start_date: null,
        baseline_due_date: null,
        drift_days: null,
        wbs_node_id: null,
    stage_id: 1,
        priority: "MEDIUM",
        assignee: "Иванова Анна",
        is_done: false,
        is_overdue: false,
        is_due_soon: false,
        risk_level: null,
        risk_reasons: [],
        updated_at: "2026-09-01T00:00:00Z", revision: 1,
        ...overrides,
    };
}

function calendar(overrides: Partial<ProjectCalendar>): ProjectCalendar {
    return {
        range: { date_from: "2026-01-01", date_to: "2026-12-31", today: "2026-09-30" },
        project: { start_date: null, due_date: null },
        tasks: [],
        stages: [],
        assignees: [],
        summary: { overdue: 0, due_soon: 0, unscheduled: 0, drifted: 0, dependency_risks: 0 },
        recent_changes: [],
        milestones: [],
        dependencies: [],
        ...overrides,
    } as ProjectCalendar;
}

describe("масштаб шкалы", () => {
    it("Ctrl+колесо: вниз — мельче, вверх — крупнее, на краях стоит", () => {
        expect(nextTimelineScale("month", 1)).toBe("quarter");
        expect(nextTimelineScale("month", -1)).toBe("week");
        expect(nextTimelineScale("year", 1)).toBe("year");
        expect(nextTimelineScale("week", -3)).toBe("week");
        expect(nextTimelineScale("quarter", 0)).toBe("quarter");
    });

    it("«Уместить»: самый крупный масштаб, который влезает", () => {
        expect(fitTimelineScale(10, 1200)).toBe("week"); // 10 × 104 = 1040
        expect(fitTimelineScale(30, 1200)).toBe("month"); // 30 × 38 = 1140
        expect(fitTimelineScale(55, 1200)).toBe("quarter"); // 55 × 20 = 1100
        expect(fitTimelineScale(900, 1200)).toBe("year");
    });
});

describe("растяжка в режиме показа", () => {
    it("ширина дня — сколько влезает, подписи — по ближайшему масштабу", () => {
        expect(stretchDayWidth(80, 1130)).toEqual({ scale: "quarter", dayWidth: 14 });
        expect(stretchDayWidth(20, 1200)).toEqual({ scale: "month", dayWidth: 60 });
        expect(stretchDayWidth(5, 1200)).toEqual({ scale: "week", dayWidth: 104 });
        expect(stretchDayWidth(900, 1200)).toEqual({ scale: "year", dayWidth: 4 });
    });
});

describe("весь срок проекта", () => {
    it("от раннего начала до позднего срока задач, вех и проекта, с запасом 2 дня", () => {
        const span = projectSpan({
            project: { start_date: "2026-03-01", due_date: "2026-06-30" },
            tasks: [
                { start_date: "2026-02-20", due_date: "2026-03-10" },
                { start_date: null, due_date: "2026-07-05" },
                { start_date: null, due_date: null },
            ],
            milestones: [{ due_date: "2026-07-01" }],
        });
        expect(span).toEqual({ dateFrom: "2026-02-18", dateTo: "2026-07-07" });
    });

    it("нет дат — null; ошибочная дата не растягивает шкалу на века", () => {
        expect(projectSpan({ project: { start_date: null, due_date: null }, tasks: [], milestones: [] })).toBeNull();
        const span = projectSpan({ project: { start_date: "2026-01-01", due_date: "2099-01-01" }, tasks: [], milestones: [] });
        expect(span && rangeDays(span)).toBe(5 * 366 + 1);
    });

    it("объединение периодов и нарезка на куски по 371 дню", () => {
        expect(unionRange({ dateFrom: "2026-09-01", dateTo: "2026-09-30" }, { dateFrom: "2026-08-31", dateTo: "2026-09-06" })).toEqual({
            dateFrom: "2026-08-31",
            dateTo: "2026-09-30",
        });
        expect(splitRange({ dateFrom: "2026-01-01", dateTo: "2026-01-31" })).toEqual([{ dateFrom: "2026-01-01", dateTo: "2026-01-31" }]);
        const chunks = splitRange({ dateFrom: "2026-01-01", dateTo: "2027-12-31" });
        expect(chunks).toHaveLength(2);
        expect(chunks[0]).toEqual({ dateFrom: "2026-01-01", dateTo: "2027-01-06" });
        expect(chunks[1]).toEqual({ dateFrom: "2027-01-07", dateTo: "2027-12-31" });
    });

    it("склейка кусков: без повторов задач, зависимостей и вех", () => {
        const first = calendar({
            tasks: [task({ id: 1 }), task({ id: 2 })],
            milestones: [{ id: 1, title: "Пилот", due_date: "2026-12-30", status: "PLANNED", wbs_node_id: null, description_md: null, is_system: false }],
            dependencies: [{ id: 5 } as ProjectCalendar["dependencies"][number]],
        });
        const second = calendar({
            range: { date_from: "2027-01-01", date_to: "2027-06-30", today: "2026-09-30" },
            tasks: [task({ id: 2 }), task({ id: 3 })],
            milestones: [{ id: 1, title: "Пилот", due_date: "2026-12-30", status: "PLANNED", wbs_node_id: null, description_md: null, is_system: false }],
            dependencies: [{ id: 5 } as ProjectCalendar["dependencies"][number]],
        });
        const merged = mergeProjectCalendars([first, second]);
        expect(merged.tasks.map((item) => item.id)).toEqual([1, 2, 3]);
        expect(merged.milestones).toHaveLength(1);
        expect(merged.dependencies).toHaveLength(1);
        expect(merged.range.date_to).toBe("2027-06-30");
        expect(mergeProjectCalendars([first])).toBe(first);
    });
});

describe("легенда и колонки", () => {
    const stages = new Map<number, CalendarStage>([
        [1, { id: 1, name: "Анализ", color: "#0B63CE", order_index: 0, is_done_stage: false } as CalendarStage],
        [2, { id: 2, name: "Разработка", color: "#1F7A3A", order_index: 1, is_done_stage: false } as CalendarStage],
        [3, { id: 3, name: "Готово", color: "#999999", order_index: 2, is_done_stage: true } as CalendarStage],
    ]);

    it("по этапу — только этапы с задачами, в порядке доски, цвет через dataColor", () => {
        const legend = timelineLegend("stage", [task({ stage_id: 2 }), task({ id: 2, stage_id: 1 })], stages);
        expect(legend.title).toBe("Цвет по этапу");
        expect(legend.items.map((item) => item.label)).toEqual(["Анализ", "Разработка"]);
        expect(legend.items[0].color).toBe("#0B63CE");
    });

    it("по исполнителю — по алфавиту, «Без исполнителя» в конце; по статусу — фиксированный набор", () => {
        const legend = timelineLegend("assignee", [task({ assignee: null }), task({ id: 2, assignee: "Петров Олег" }), task({ id: 3 })], stages);
        expect(legend.items.map((item) => item.label)).toEqual(["Иванова Анна", "Петров Олег", "Без исполнителя"]);
        expect(legend.items[2].color).toBe("var(--color-text-disabled)");
        expect(timelineLegend("status", [], stages).items.map((item) => item.label)).toContain("Просрочено");
    });

    it("инициалы, ширина колонки, доля разделителя", () => {
        expect(assigneeInitials("иванова анна сергеевна")).toBe("ИА");
        expect(assigneeInitials("a.ivanova")).toBe("A");
        expect(assigneeInitials("  ")).toBeNull();
        expect(assigneeInitials(null)).toBeNull();
        expect(clampLabelWidth(100)).toBe(180);
        expect(clampLabelWidth(999)).toBe(480);
        expect(clampLabelWidth("x")).toBe(210);
        expect(clampSplitRatio(0.05)).toBe(0.2);
        expect(clampSplitRatio(null)).toBe(0.5);
    });

    it("колонки: порядок фиксированный, мусор из хранилища отбрасывается, значения ячеек", () => {
        expect(normalizeGanttColumns(["days", "assignee", "мусор"])).toEqual(["assignee", "days"]);
        expect(normalizeGanttColumns("assignee")).toEqual([]);
        expect(ganttColumnsWidth(["start", "due"])).toBe(100);
        const item = task({ start_date: "2026-09-01", due_date: "2026-09-10" });
        expect(ganttCellValue(item, "start")).toBe("01.09");
        expect(ganttCellValue(item, "due")).toBe("10.09");
        expect(ganttCellValue(item, "days")).toBe("10");
        expect(ganttCellValue(task({ start_date: null, due_date: "2026-09-10" }), "days")).toBe("1");
        expect(ganttCellValue(task({ assignee: null }), "assignee")).toBe("—");
    });

    it("выгрузка: имя файла и страница PDF под картинку", () => {
        expect(exportFileName("DEMO", "2026-09-30", "png")).toBe("DEMO-гант-2026-09-30.png");
        expect(exportFileName("a/b", "2026-09-30", "pdf")).toBe("a_b-гант-2026-09-30.pdf");
        const page = pdfPageSize(3000, 800);
        expect(page.orientation).toBe("l");
        expect(page.height).toBeCloseTo(211.7, 1); // 800 px при 96 dpi — уже больше A4 по короткой стороне
        expect(page.width / page.height).toBeCloseTo(3000 / 800, 1);
        const small = pdfPageSize(400, 300);
        expect(Math.min(small.width, small.height)).toBeCloseTo(210, 0);
    });
});
