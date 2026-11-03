import { describe, expect, it } from "vitest";
import { projectTaskFocus } from "@/lib/dashboard";

const stages = [{ stage_id: 1, is_done_stage: false }, { stage_id: 2, is_done_stage: false }, { stage_id: 3, is_done_stage: true }];
const task = (id: number, stage_id: number | null, due_date: string | null) => ({ id, stage_id, due_date });

describe("projectTaskFocus", () => {
    it("не считает завершённые и неизвестные стадии просроченными", () => {
        const focus = projectTaskFocus([
            task(1, 1, "2026-11-01"), task(2, 3, "2026-10-01"), task(3, 99, "2026-10-01"), task(4, null, "2026-10-01"),
        ], stages, "2026-11-02");
        expect(focus.overdue.map(item => item.id)).toEqual([1]);
        expect(focus.scheduled.map(item => item.id)).toEqual([1]);
    });

    it("разделяет просрочки и ближайшие семь дней, включая сегодня и границу", () => {
        const tasks = [task(1, 1, "2026-11-10"), task(2, 2, "2026-11-09"), task(3, 1, "2026-11-02"), task(4, 2, "2026-11-01")];
        const focus = projectTaskFocus(tasks, stages, "2026-11-02");
        expect(focus.dueSoon.map(item => item.id)).toEqual([3, 2]);
        expect(focus.overdue.map(item => item.id)).toEqual([4]);
        expect(focus.scheduled.map(item => item.id)).toEqual([4, 3, 2, 1]);
        expect(tasks.map(item => item.id)).toEqual([1, 2, 3, 4]);
    });

    it("считает отсутствие срока только у открытых задач и не сортирует некорректные даты", () => {
        const focus = projectTaskFocus([task(1, 1, null), task(2, 3, null), task(3, 1, "не дата")], stages, "2026-11-02");
        expect(focus.withoutDeadline.map(item => item.id)).toEqual([1]);
        expect(focus.scheduled).toEqual([]);
    });

    it("не подменяет текущее состояние исторической датой завершения", () => {
        const reopened = { ...task(1, 2, "2026-11-01"), completed_at: "2026-10-01T10:00:00Z" };
        expect(projectTaskFocus([reopened], stages, "2026-11-02").overdue).toEqual([reopened]);
        expect(projectTaskFocus([reopened], [], "2026-11-02").scheduled).toEqual([]);
    });
});
