import { describe, expect, it } from "vitest";
import { assigneeGroupKey, buildTimelineAssigneeRows, buildTimelineStageRows } from "@/lib/timelineGroups";
import type { CalendarStage, CalendarTask } from "@/lib/types";

const stage = (id: number, order: number): CalendarStage => ({ id, name: `Стадия ${id}`, color: "#7C1623", order_index: order, is_done_stage: false });
const task = (id: number, stageId: number, start: string | null, done = false): CalendarTask => ({
    id, key: `T-${id}`, title: `Задача ${id}`, start_date: start, due_date: start, baseline_start_date: null, baseline_due_date: null,
    drift_days: null, wbs_node_id: null, stage_id: stageId, priority: "MEDIUM", assignee: null, is_done: done, is_overdue: false, is_due_soon: false,
    risk_level: null, risk_reasons: [], updated_at: "2026-09-01T00:00:00Z",
});

describe("buildTimelineStageRows", () => {
    it("группирует задачи по стадиям в порядке доски и пропускает пустые", () => {
        const rows = buildTimelineStageRows([stage(2, 1), stage(1, 0), stage(3, 2)], [task(10, 2, "2026-09-03"), task(11, 1, "2026-09-05", true), task(12, 2, "2026-09-01")], new Set());
        expect(rows.map((row) => row.id)).toEqual(["group:1", "task:11", "group:2", "task:12", "task:10"]);
        const group = rows[2];
        expect(group.kind === "group" && group.aggregate).toMatchObject({ total: 2, startDate: "2026-09-01", dueDate: "2026-09-03" });
    });

    it("свёрнутая группа прячет задачи, неизвестная стадия уходит в «Без стадии»", () => {
        const rows = buildTimelineStageRows([stage(1, 0)], [task(10, 1, null), task(11, 99, null)], new Set([1]));
        expect(rows.map((row) => row.id)).toEqual(["group:1", "group:other", "task:11"]);
    });
});

describe("buildTimelineAssigneeRows", () => {
    const assigned = (id: number, assignee: string | null, start: string) => ({ ...task(id, 1, start), assignee });

    it("показывает только реальную identity исполнителя и не угадывает фото по совпавшему имени", () => {
        const person = { id: 7, username: "ivan", first_name: "Иван", last_name: "Петров", middle_name: null, has_avatar: true };
        const first = { ...assigned(1, "Иван Петров", "2026-09-01"), executor: person };
        const other = { ...assigned(2, "Иван Петров", "2026-09-02"), executor: { ...person, id: 8 } };
        const group = buildTimelineAssigneeRows([first], new Set())[0];
        expect(group.kind === "group" && group.user?.id).toBe(7);
        const ambiguous = buildTimelineAssigneeRows([first, other], new Set())[0];
        expect(ambiguous.kind === "group" && ambiguous.user).toBeUndefined();
        const snapshot = buildTimelineAssigneeRows([assigned(3, "Иван Петров", "2026-09-03")], new Set())[0];
        expect(snapshot.kind === "group" && snapshot.user).toBeUndefined();
    });

    it("строка на человека по алфавиту, участник без задач тоже есть, «Без исполнителя» — в конце", () => {
        const rows = buildTimelineAssigneeRows(
            [assigned(1, "Яков", "2026-09-02"), assigned(2, null, "2026-09-01"), assigned(3, "Анна", "2026-09-03"), assigned(4, "Яков", "2026-09-01")],
            new Set(),
            ["Борис", "Анна"],
        );
        expect(rows.map((row) => row.id)).toEqual([
            "group:assignee:Анна", "task:3",
            "group:assignee:Борис",
            "group:assignee:Яков", "task:4", "task:1",
            "group:assignee:", "task:2",
        ]);
        const unassigned = rows.at(-2);
        expect(unassigned?.kind === "group" && [unassigned.title, unassigned.assignee]).toEqual(["Без исполнителя", null]);
    });

    it("свёрнутый человек прячет задачи, но строка со сводкой остаётся", () => {
        const rows = buildTimelineAssigneeRows([assigned(1, "Анна", "2026-09-02")], new Set([assigneeGroupKey("Анна")]));
        expect(rows.map((row) => row.id)).toEqual(["group:assignee:Анна"]);
        expect(rows[0].kind === "group" && rows[0].aggregate.total).toBe(1);
    });
});
