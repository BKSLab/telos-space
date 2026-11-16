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
        await page.goto("/?view=summary");
        const metrics = page.getByRole("region", { name: "Показатели портфеля" });
        await expect(metrics.getByText("50%", { exact: true })).toHaveCount(0);
        await expect(metrics.getByText("Выполнено", { exact: true }).locator("..").locator("..").getByText("1", { exact: true })).toHaveCSS("font-size", "32px");
        await expect(metrics.getByText("900", { exact: true })).toHaveCount(0);
        await expect(page.getByRole("img", { name: "Выполнено 1 из 2 задач: 50%" })).toBeVisible();
        await expect(page.getByRole("region", { name: "Каталог проектов" })).toHaveCount(0);
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
        await expect(page.getByRole("region", { name: "Показатели проекта" }).getByText("50%", { exact: true })).toHaveCount(0);
        await expect(page.getByRole("heading", { name: "Сроки и вехи", exact: true })).toBeVisible();
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

test("срок проекта и ближайшая реальная веха, загрузка и отсутствие логотипа на 1536px", async ({ page }, info) => {
    await page.setViewportSize({ width: 1536, height: 1024 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    const state = await installOverviewApi(page);
    state.projects[0].due_date = "2999-12-31";
    state.projects[0].icon = null;
    state.projects[0].icon_image_version = "broken-image";
    await page.route("**/api/v1/projects/1/icon-image*", route => route.fulfill({ status: 404, json: { detail: "Нет изображения" } }));
    let release = () => {};
    const ready = new Promise<void>(resolve => { release = resolve; });
    await page.route("**/api/v1/projects/1/milestones", async route => {
        await ready;
        await route.fulfill({ json: [
            { id: 1, project_id: 1, title: "Выполненная контрольная точка", due_date: "2019-01-01", status: "ACHIEVED" },
            { id: 3, project_id: 1, title: "Следующая контрольная точка", due_date: "2999-03-01", status: "PLANNED" },
            { id: 2, project_id: 1, title: "Проверка готовности проекта", due_date: "2999-02-01", status: "PLANNED" },
        ] });
    });
    try {
        await page.goto("/projects/DEMO");
        await expect(page.getByRole("status", { name: "Загрузка вех" })).toBeVisible();
        await expect(page.locator(".project-overview-header").getByTestId("project-icon-default")).toBeVisible();
    } finally { release(); }
    const schedule = page.getByRole("region", { name: "Сроки и вехи" });
    await expect(schedule.getByRole("link", { name: /Проверка готовности проекта/ })).toBeVisible();
    await expect(schedule.getByText("Выполненная контрольная точка")).toHaveCount(0);
    await expect(schedule.getByText("Осталось вех: 2. Все даты — в календаре.")).toBeVisible();
    await expect(page.locator(".project-overview-header").getByTestId("project-icon-default")).toHaveCSS("width", "72px");
    await page.screenshot({ path: info.outputPath("overview-1536.png") });
    await page.getByText(/Полный разбор ·/).scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath("overview-insight-1536.png") });
    expect(state.errors).toEqual([]);
});

test("при ошибке вех сохраняет живые показатели и повторяет запрос", async ({ page }) => {
    await installOverviewApi(page);
    let failed = true;
    await page.route("**/api/v1/projects/1/milestones", route => route.fulfill(failed
        ? { status: 403, json: { detail: "Вехи недоступны" } }
        : { json: [] }));
    await page.goto("/projects/DEMO");
    await expect(page.getByText("Вехи недоступны", { exact: true }).first()).toBeVisible();
    await expect(page.getByRole("img", { name: "Выполнено 1 из 2 задач: 50%" })).toBeVisible();
    failed = false;
    await page.getByRole("button", { name: "Повторить загрузку вех" }).click();
    await expect(page.getByText("Вехи пока не заданы")).toBeVisible();
});

test("пустой проект и отсутствие разбора не изображают выполненную работу на 320px", async ({ page }, info) => {
    await page.setViewportSize({ width: 320, height: 740 });
    await installOverviewApi(page);
    await page.route("**/api/v1/projects/1/stats", route => route.fulfill({ json: { ...stats, total_tasks: 0, done_tasks: 0, in_progress_tasks: 0, overdue_tasks: 0, unassigned_tasks: 0, completion_rate: 0, stage_breakdown: [] } }));
    await page.route("**/api/v1/projects/1/tasks", route => route.fulfill({ json: [] }));
    await page.route("**/api/v1/dashboard/analytics*", route => route.fulfill({ json: null }));
    await page.goto("/projects/DEMO");
    await expect(page.getByRole("img", { name: "Задач пока нет" })).toBeVisible();
    await expect(page.getByText("В проекте пока нет задач", { exact: true })).toBeVisible();
    await expect(page.getByText("100%", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Разобрать", exact: true }).scrollIntoViewIfNeeded();
    await expect(page.getByText(/Попросите Ариса оценить ход работ/)).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath("overview-empty-320.png") });
});

for (const viewport of [{ width: 320, height: 568 }, { width: 844, height: 390 }]) {
    test(`длинная шапка оставляет доступ к обзору на ${viewport.width}x${viewport.height}`, async ({ page }) => {
        await page.setViewportSize(viewport);
        const state = await installOverviewApi(page);
        state.projects[0].name = "ДлинноеНазваниеПроекта".repeat(13).slice(0, 255);
        await page.goto("/projects/DEMO");
        const metrics = page.getByRole("region", { name: "Показатели проекта" });
        await expect(metrics).toBeAttached();
        expect(await metrics.evaluate(element => element.closest(".overflow-y-auto")?.clientHeight ?? 0)).toBeGreaterThanOrEqual(120);
        await metrics.scrollIntoViewIfNeeded();
        await expect(metrics).toBeInViewport();
        await page.getByText(/Полный разбор ·/).scrollIntoViewIfNeeded();
        await expect(page.getByText(/Полный разбор ·/)).toBeInViewport();
        const identity = page.getByRole("group", { name: "Сведения о проекте" });
        await identity.focus();
        await page.keyboard.press("End");
        await expect.poll(() => identity.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
}

test("срок и веха доступны при ошибке независимой статистики", async ({ page }) => {
    const state = await installOverviewApi(page);
    state.projects[0].due_date = "2999-12-31";
    await page.route("**/api/v1/projects/1/stats", route => route.fulfill({ status: 403, json: { detail: "Показатели недоступны" } }));
    await page.route("**/api/v1/projects/1/milestones", route => route.fulfill({ json: [{ id: 1, project_id: 1, title: "Независимая веха", due_date: "2999-02-01", status: "PLANNED" }] }));
    await page.goto("/projects/DEMO");
    await expect(page.getByRole("region", { name: "Сроки и вехи" }).getByText("Срок проекта")).toBeVisible();
    await expect(page.getByRole("link", { name: /Независимая веха/ })).toBeVisible();
    await expect(page.getByRole("img", { name: /Выполнено .* задач:/ })).toHaveCount(0);
});
