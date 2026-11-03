import { expect, test, type Page } from "@playwright/test";
import type { AnalyticsReport, DashboardProject, ProjectStats } from "../src/lib/types";
import { installMockApi, mockState } from "./support/taskMockApi";

const stats: ProjectStats = {
    project_id: 1, total_tasks: 2, done_tasks: 1, in_progress_tasks: 1, overdue_tasks: 1,
    due_soon_tasks: 0, unassigned_tasks: 2, completion_rate: 0.5, next_due_date: null,
    stage_breakdown: [
        { stage_id: 1, stage_name: "В работе", color: "#58a6ff", is_done_stage: false, tasks_count: 1 },
        { stage_id: 2, stage_name: "Готово", color: "#3fb950", is_done_stage: true, tasks_count: 1 },
    ],
};
const report: AnalyticsReport = {
    id: 1, scope: "PORTFOLIO", project_id: null, project_key: null, project_name: null,
    created_at: "2020-01-01T10:00:00Z", created_by: "Тестов Тест", llm_model: "test", duration_ms: 100,
    headline: "Исторический вывод Ариса", health: "CRITICAL", health_note: "Проверьте зависимости задач.",
    findings: [], progress: [], recommendations: [],
    signals: { total_tasks: 900, done_tasks: 0, overdue_tasks: 900, due_soon_tasks: 0, no_due_date_tasks: 0, unassigned_tasks: 0, stale_tasks: 0, blocked_tasks: 0, milestones_at_risk: 0, unplaced_tasks: 0 },
    context: { projects: 1, tasks_total: 900, tasks_included: 900, comments_included: 0, documents_included: 0, stickers_included: 0, milestones_included: 0, activity_included: 0, truncated: false, omitted: [], wbs_nodes_included: 0 },
};

async function installOverviewApi(page: Page) {
    const state = mockState();
    state.projects[0].description_md = "## Описание\n\n**Важный этап**";
    state.stages.push({ ...state.stages[0], id: 2, name: "Готово", is_done_stage: true, order_index: 1, color: "#3fb950" });
    state.tasks[0].due_date = "2020-01-01";
    state.tasks[1].stage_id = 2;
    state.tasks[1].due_date = "2019-01-01";
    state.tasks[1].title = "Завершённая со старым сроком";
    state.tasks.splice(2);
    await installMockApi(page, state);
    await page.route("**/api/v1/**", async route => {
        const request = route.request();
        const url = new URL(request.url());
        const path = url.pathname.replace("/api/v1", "");
        if (request.method() === "OPTIONS") return route.fallback();
        const headers = { "access-control-allow-origin": request.headers()["origin"] ?? "http://127.0.0.1:4179", "access-control-allow-credentials": "true" };
        const reply = (json: unknown) => route.fulfill({ headers, json });
        if (path === "/dashboard/analytics") return reply({ ...report, ...(url.searchParams.has("project_id") ? { scope: "PROJECT", project_id: 1, project_key: "DEMO", project_name: state.projects[0].name } : {}) });
        if (path === "/projects/1/stats") return reply(stats);
        if (path === "/projects/1/risks/summary") return reply({ total_risks: 0, active_risks: 0, high_risks: 0, occurred_risks: 0, risks_due_for_review: 0, signals: [] });
        if (path === "/dashboard") {
            const projects: DashboardProject[] = state.projects.map(project => ({ ...project, total_tasks: project.id === 1 ? 2 : 0, done_tasks: project.id === 1 ? 1 : 0, in_progress_tasks: project.id === 1 ? 1 : 0, overdue_tasks: project.id === 1 ? 1 : 0, completion_rate: project.id === 1 ? 0.5 : 0, next_due_date: null }));
            return reply({ projects, totals: { total_projects: 3, active_projects: 1, total_tasks: 2, done_tasks: 1, in_progress_tasks: 1, overdue_tasks: 1, completion_rate: 0.5 },
                attention_tasks: [{ ...state.tasks[0], project_key: "DEMO", project_name: state.projects[0].name, project_color: state.projects[0].color, stage_name: "В работе", is_overdue: true }], recent_tasks: [], has_outside_tasks: false });
        }
        return route.fallback();
    });
    return state;
}

for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }, { width: 320, height: 740 }]) {
    test(`обзоры портфеля и проекта: факты, исторический разбор, мобильная ширина ${viewport.width}`, async ({ page }, info) => {
        await page.setViewportSize(viewport);
        const state = await installOverviewApi(page);
        await page.goto("/");
        const metrics = page.getByRole("region", { name: "Показатели портфеля" });
        await expect(metrics.getByText("50%", { exact: true })).toHaveCSS("font-size", "32px");
        await expect(metrics.getByText("900", { exact: true })).toHaveCount(0);
        await expect(page.getByRole("img", { name: "Выполнено 1 из 2 задач: 50%" })).toBeVisible();
        await expect(page.getByText("Описание Важный этап", { exact: true })).toBeVisible();
        await expect(page.locator("details").filter({ has: page.getByText(/Полный разбор ·/) })).not.toHaveAttribute("open");
        await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

        await page.getByRole("button", { name: /Согласовать макеты личного кабинета/ }).click();
        const task = page.getByRole("dialog", { name: "Задача DEMO-1", exact: true });
        await expect(task).toBeVisible();
        await page.keyboard.press("Escape");
        await expect(task).toBeHidden();

        await page.locator("#main-content > div").evaluate(element => { element.scrollTop = 0; });
        await page.screenshot({ path: info.outputPath(`portfolio-${viewport.width}.png`) });
        await page.goto("/projects/DEMO");
        await expect(page.getByRole("region", { name: "Показатели проекта" }).getByText("50%", { exact: true })).toBeVisible();
        await expect(page.getByRole("progressbar", { name: "Доля задач в стадии «В работе»" })).toHaveAttribute("aria-valuenow", "50");
        await expect(page.getByRole("button", { name: /Завершённая со старым сроком/ })).toHaveCount(0);
        await page.screenshot({ path: info.outputPath(`project-overview-${viewport.width}.png`) });
        await page.getByText(/Полный разбор ·/).click();
        await expect(page.getByText("Сигналы на момент разбора", { exact: true })).toBeVisible();
        await expect(page.getByText("900", { exact: true })).toBeVisible();
        await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
        expect(state.errors).toEqual([]);
    });
}
