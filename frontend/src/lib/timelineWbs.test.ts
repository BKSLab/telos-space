import { describe, expect, it } from "vitest";
import { buildTimelineWbsRows } from "@/lib/timelineWbs";
import type { CalendarTask, CalendarWbsNode } from "@/lib/types";

const nodes: CalendarWbsNode[] = [
    { id: 1, parent_id: null, title: "Backend", position: 1000 },
    { id: 2, parent_id: 1, title: "API", position: 1000 },
];

function task(id: number, nodeId: number | null, done = false): CalendarTask {
    return {
        id,
        key: `PROJ-${id}`,
        title: `Задача ${id}`,
        start_date: "2026-09-01",
        due_date: "2026-09-05",
        baseline_start_date: null,
        baseline_due_date: null,
        drift_days: null,
        stage_id: 1,
        wbs_node_id: nodeId,
        priority: "MEDIUM",
        assignee: null,
        is_done: done,
        is_overdue: false,
        is_due_soon: !done,
        risk_level: done ? null : "medium",
        risk_reasons: [],
        updated_at: "2026-09-02T00:00:00Z", revision: 1,
    };
}

describe("timeline WBS rows", () => {
    it("keeps structural order and places tasks under their sections", () => {
        const rows = buildTimelineWbsRows(nodes, [task(1, 1), task(2, 2), task(3, null)], new Set());

        expect(rows.map((row) => row.id)).toEqual([
            "node:1",
            "task:1",
            "node:2",
            "task:2",
            "node:unassigned",
            "task:3",
        ]);
    });

    it("collapses a subtree and exposes its aggregate", () => {
        const rows = buildTimelineWbsRows(nodes, [task(1, 1, true), task(2, 2)], new Set([1]));

        expect(rows).toHaveLength(1);
        const root = rows[0];
        expect(root.kind).toBe("node");
        if (root.kind === "node") {
            expect(root.aggregate).toMatchObject({ total: 2, done: 1, progress: 50, risks: 1 });
            expect(root.aggregate.startDate).toBe("2026-09-01");
            expect(root.aggregate.dueDate).toBe("2026-09-05");
        }
    });

    it("includes a task with an open end in the subtree dates", () => {
        const openTask = { ...task(1, 1), start_date: "2026-09-03", due_date: null };

        const rows = buildTimelineWbsRows(nodes, [openTask], new Set([1]));

        const root = rows[0];
        expect(root.kind).toBe("node");
        if (root.kind === "node") {
            expect(root.aggregate.startDate).toBe("2026-09-03");
            expect(root.aggregate.dueDate).toBe("2026-09-03");
        }
    });
});
