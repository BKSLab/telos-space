// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import type { Project, ProjectMilestone } from "@/lib/types";
import { ProjectSchedulePanel } from "./ProjectSchedulePanel";

const project: Project = {
    id: 1, key: "DEMO", name: "Проект", color: "#0078d4", icon: null, description_md: null,
    status: "ACTIVE", due_date: "2999-12-31", start_date: null, order_index: 0, created_at: "2026-01-01", updated_at: "2026-01-01",
};
function milestone(id: number, title: string, due_date: string, status: ProjectMilestone["status"] = "PLANNED"): ProjectMilestone {
    return { id, project_id: 1, title, due_date, status, description_md: null, wbs_node_id: null, created_at: "2026-01-01", updated_at: "2026-01-01" };
}
function show(props: Partial<Parameters<typeof ProjectSchedulePanel>[0]> = {}) {
    return render(<MemoryRouter><ProjectSchedulePanel project={project} nextTaskDue={null} milestones={[]} isPending={false} error={null} onRetry={vi.fn()} {...props} /></MemoryRouter>);
}
afterEach(cleanup);

it("показывает ближайшую недостигнутую веху, включая просроченную, и сохраняет исходный порядок данных", () => {
    const milestones = [milestone(3, "Будущая веха", "2999-01-01"), milestone(1, "Достигнутая", "2019-01-01", "ACHIEVED"), milestone(2, "Незавершённый этап", "2020-01-01")];
    show({ milestones });
    expect(screen.getByRole("link", { name: /Незавершённый этап/ }).getAttribute("href")).toBe("/projects/DEMO/calendar");
    expect(screen.getByTitle(/Срок:.*просрочен/)).toBeTruthy();
    expect(screen.queryByText("Достигнутая")).toBeNull();
    expect(screen.getByText("Осталось вех: 2. Все даты — в календаре.")).toBeTruthy();
    expect(milestones.map(item => item.id)).toEqual([3, 1, 2]);
});

it("различает отсутствие вех и достигнутые вехи, не рисует выдуманные этапы", () => {
    const view = show({ project: { ...project, due_date: null } });
    expect(screen.getByText("Не задан")).toBeTruthy();
    expect(screen.getByText("Вехи пока не заданы")).toBeTruthy();
    view.unmount();
    show({ milestones: [milestone(1, "Готово", "2026-01-01", "ACHIEVED")] });
    expect(screen.getByText("Все вехи достигнуты")).toBeTruthy();
    expect(screen.queryByText("Ближайшая веха")).toBeNull();
});

it("сохраняет срок проекта при ошибке вех и позволяет повторить запрос", async () => {
    const onRetry = vi.fn();
    show({ error: new Error("Нет соединения"), onRetry });
    expect(screen.getByText("Срок проекта")).toBeTruthy();
    expect(screen.getByText("Вехи недоступны")).toBeTruthy();
    expect(screen.queryByText("Вехи пока не заданы")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Повторить загрузку вех" }));
    expect(onRetry).toHaveBeenCalledOnce();
});

it("не подменяет загрузку пустым состоянием", () => {
    show({ milestones: undefined, isPending: true });
    expect(screen.getByRole("status", { name: "Загрузка вех" })).toBeTruthy();
    expect(screen.queryByText("Вехи пока не заданы")).toBeNull();
});
