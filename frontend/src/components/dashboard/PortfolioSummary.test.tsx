// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { PortfolioSummary } from "@/components/dashboard/PortfolioSummary";
import { PortfolioMetrics } from "@/components/dashboard/PortfolioMetrics";
import { queryKeys } from "@/lib/api";
import type { Dashboard, DashboardProject, DashboardTask, ProjectStatus } from "@/lib/types";
import type { RiskSummary } from "@/lib/risks";
import { useUiStore } from "@/stores/ui";

const project: DashboardProject = {
    id: 1, key: "ACTIVE", name: "Рабочий проект", status: "ACTIVE", description_md: null, color: "#58a6ff", icon: null,
    total_tasks: 8, done_tasks: 2, in_progress_tasks: 3, overdue_tasks: 1, completion_rate: 0.25,
    next_due_date: "2999-01-02", updated_at: "2026-11-01T10:00:00Z",
};
const risks: RiskSummary = {
    total_risks: 5, active_risks: 3, open_risks: 2, mitigating_risks: 1, occurred_risks: 1, closed_risks: 1,
    high_risks: 2, medium_risks: 2, low_risks: 1, risks_without_owner: 0, risks_without_mitigation: 0,
    risks_due_for_review: 0, risks_review_overdue: 0, risks_linked_to_tasks: 0, ai_suggested_risks: 0,
    latest_update: null, matrix: [], signals: [],
};
const dashboard: Dashboard = {
    totals: { total_projects: 6, active_projects: 2, total_tasks: 8, done_tasks: 2, in_progress_tasks: 3, overdue_tasks: 1, completion_rate: 0.25 },
    projects: ["ACTIVE", "ACTIVE", "PLANNING", "PAUSED", "COMPLETED", "ARCHIVED"].map((status, index) => ({
        ...project, id: index + 1, key: `P${index}`, name: `Проект ${index}`, status: status as ProjectStatus,
    })),
    attention_tasks: [], recent_tasks: [], has_outside_tasks: false,
};
const clients: QueryClient[] = [];
function setup(data = dashboard) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    clients.push(client);
    client.setQueryData(queryKeys.dashboard, data);
    client.setQueryData(queryKeys.myTaskList("all", false), []);
    client.setQueryData(queryKeys.dashboardAnalytics(null), null);
    return render(<QueryClientProvider client={client}><MemoryRouter><PortfolioSummary /></MemoryRouter></QueryClientProvider>);
}
function task(id: number): DashboardTask {
    return { id, key: `ACTIVE-${id}`, title: `Проверить договор ${id}`, project_id: 1, project_key: "ACTIVE", project_name: project.name,
        project_color: project.color, stage_id: 1, stage_name: "В работе", priority: "HIGH", due_date: "2020-01-01", is_overdue: true, updated_at: project.updated_at };
}
afterEach(() => {
    cleanup();
    clients.splice(0).forEach(client => client.clear());
    useUiStore.setState({ selectedTaskId: null });
});

describe("графическая сводка портфеля", () => {
    it("строит распределение по всем текущим статусам и не дублирует каталог", () => {
        setup();
        const distribution = within(screen.getByRole("region", { name: "Проекты по статусам" }));
        expect(distribution.getByRole("img", { name: "Статусы проектов: всего 6" })).toBeTruthy();
        expect(distribution.getByText("В работе")).toBeTruthy();
        expect(distribution.getByText("Планирование")).toBeTruthy();
        expect(distribution.getByText("На паузе")).toBeTruthy();
        expect(distribution.getByText("Завершён")).toBeTruthy();
        expect(distribution.getByText("В архиве")).toBeTruthy();
        for (const [label, count] of [["В работе", 2], ["Планирование", 1], ["На паузе", 1], ["Завершён", 1], ["В архиве", 1]] as const) {
            expect(distribution.getByText(label).closest("dt")?.nextElementSibling?.textContent).toBe(String(count));
        }
        expect(screen.queryByRole("region", { name: "Каталог проектов" })).toBeNull();
        expect(screen.queryByRole("list", { name: "Проекты портфеля" })).toBeNull();
        expect(screen.getByRole("img", { name: "Выполнено 2 из 8 задач: 25%" })).toBeTruthy();
    });

    it("отличает отсутствие оценки рисков от настоящего нуля и поясняет частичный охват", () => {
        const initial = render(<PortfolioMetrics data={dashboard} />);
        const metrics = within(screen.getByRole("region", { name: "Показатели портфеля" }));
        expect(metrics.getByText("Нет данных об оценке")).toBeTruthy();
        expect(metrics.getByText("—")).toBeTruthy();
        initial.rerender(<PortfolioMetrics data={{ ...dashboard, projects: [{ ...project, risks }, { ...project, id: 2 }] }} />);
        expect(metrics.getByText("Оценка: 1 из 2 проектов")).toBeTruthy();
        expect(metrics.getByText("3")).toBeTruthy();
        initial.rerender(<PortfolioMetrics data={{ ...dashboard, projects: [{ ...project, risks: { ...risks, active_risks: 0, high_risks: 0 } }] }} />);
        expect(metrics.getByText("0")).toBeTruthy();
        expect(metrics.getByText("Высоких: 0")).toBeTruthy();
    });

    it("упорядочивает реальные будущие сроки и исключает завершённые проекты и старые даты", async () => {
        setup({ ...dashboard, projects: [
            { ...project, name: "Позже", next_due_date: "2999-01-05" },
            { ...project, id: 2, key: "FIRST", name: "Раньше", next_due_date: "2999-01-01" },
            { ...project, id: 3, name: "Завершённый", status: "COMPLETED", next_due_date: "2999-01-01" },
            { ...project, id: 4, name: "Архивный", status: "ARCHIVED", next_due_date: "2999-01-01" },
            { ...project, id: 5, name: "Просроченный", next_due_date: "2020-01-01" },
            { ...project, id: 6, name: "Без срока", next_due_date: null },
        ] });
        const deadlines = within(screen.getByRole("region", { name: "Ближайшие сроки" }));
        expect(deadlines.getAllByRole("link").map(link => link.textContent)).toEqual(["РаньшеFIRST01 янв", "ПозжеACTIVE05 янв"]);
        expect(deadlines.getAllByRole("link")[0].getAttribute("href")).toBe("/projects/FIRST/tasks");
        expect(deadlines.queryByText("Просроченный")).toBeNull();
    });

    it("сохраняет открытие задач за коротким превью и в недавних изменениях", async () => {
        setup({ ...dashboard, attention_tasks: [task(1), task(2), task(3), task(4)], recent_tasks: [task(5), task(6), task(7), task(8), task(9)] });
        const attention = within(screen.getByRole("region", { name: "Требует внимания" }));
        await userEvent.click(attention.getByText("Ещё задач в подборке: 1"));
        await userEvent.click(attention.getByRole("button", { name: /Проверить договор 4/ }));
        expect(useUiStore.getState().selectedTaskId).toBe(4);
        const recent = within(screen.getByRole("region", { name: "Недавно изменённые" }));
        await userEvent.click(recent.getByText("Ещё изменений: 1"));
        await userEvent.click(recent.getByRole("button", { name: /Проверить договор 9/ }));
        expect(useUiStore.getState().selectedTaskId).toBe(9);
    });
});
