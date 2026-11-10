import { expect, test, type Page } from "@playwright/test";
import type { Dashboard, DashboardProject } from "../src/lib/types";
import { installMockApi, mockState } from "./support/taskMockApi";

async function installPortfolioApi(page: Page) {
    const state = mockState();
    state.projects[0].icon_image_version = "portfolio-v1";
    state.projects[0].description_md = "## Цель\n\n**Проверить** новое рабочее пространство";
    state.projects[2].status = "ARCHIVED";
    await installMockApi(page, state);
    await page.route("**/api/v1/projects/1/icon-image*", route => route.fulfill({ contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><rect width="64" height="64" rx="12" fill="#6c83ff"/><path d="m16 46 16-28 16 28" stroke="#fff" stroke-width="5" fill="none"/></svg>' }));
    await page.route("**/api/v1/dashboard", route => {
        const projects: DashboardProject[] = state.projects.map(project => ({ ...project, total_tasks: 4, done_tasks: 1, in_progress_tasks: 1, overdue_tasks: project.id === 1 ? 2 : 0, completion_rate: 0.25, next_due_date: "2999-01-01" }));
        const dashboard: Dashboard = { projects, totals: { total_projects: 3, active_projects: 1, total_tasks: 12, done_tasks: 3, in_progress_tasks: 3, overdue_tasks: 2, completion_rate: 0.25 }, attention_tasks: [], recent_tasks: [] };
        return route.fulfill({ json: dashboard });
    });
    await page.route("https://fonts.gstatic.com/**", route => route.abort());
    return state;
}

for (const width of [1440, 1024, 768, 390, 320]) {
    test(`каталог и сводка разделены, фильтры и крупные иконки доступны на ${width}px`, async ({ page }, info) => {
        await page.setViewportSize({ width, height: 900 });
        const state = await installPortfolioApi(page);
        await page.goto("/");
        await expect(page).toHaveURL(/\/\?view=projects$/);
        await expect(page.getByRole("heading", { name: "Портфель", exact: true })).toBeVisible();
        const catalog = page.getByRole("region", { name: "Каталог проектов" });
        const image = catalog.locator('[data-testid="project-icon-image"]:visible');
        await expect(image).toHaveCSS("width", "36px");
        await expect(image).toHaveJSProperty("naturalWidth", 64);
        if (width >= 768) {
            const table = catalog.getByRole("table", { name: "Проекты портфеля" });
            await expect(table).toBeVisible();
            expect(await table.locator("th, td").evaluateAll(cells => cells.every(cell => cell.scrollWidth <= cell.clientWidth + 1))).toBe(true);
        } else {
            await expect(catalog.getByRole("table")).toBeHidden();
            await expect(catalog.getByText("Цель Проверить новое рабочее пространство", { exact: true })).toBeVisible();
        }
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: info.outputPath(`projects-${width}.png`) });

        await page.getByRole("combobox", { name: "Статус проекта" }).selectOption("ARCHIVED");
        await expect(catalog.getByRole("status", { name: "Количество проектов" })).toHaveText("Найдено: 1 из 3");
        await page.getByRole("searchbox", { name: "Поиск проектов" }).fill("DEMO");
        await expect(catalog.getByText("Проекты не найдены", { exact: true })).toBeVisible();
        await catalog.getByRole("button", { name: "Сбросить фильтры" }).click();
        await expect(catalog.getByRole("status", { name: "Количество проектов" })).toHaveText("Всего проектов: 3");

        const navigation = page.getByRole("navigation", { name: "Представления портфеля" });
        await navigation.getByRole("link", { name: "Сводка", exact: true }).click();
        await expect(page.getByRole("region", { name: "Показатели портфеля" })).toBeVisible();
        await expect(catalog).toHaveCount(0);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await navigation.getByRole("link", { name: "Проекты", exact: true }).click();
        await expect(catalog).toBeVisible();
        expect(state.errors).toEqual([]);
    });
}

test("сворачивание списка и выбор вкладки сохраняются, текущий проект выделен", async ({ page }, info) => {
    const state = await installPortfolioApi(page);
    await page.goto("/?view=projects");
    const main = page.locator("aside").getByRole("navigation", { name: "Основная навигация" });
    await expect(main.getByRole("link", { name: "Проекты", exact: true })).toHaveCount(0);
    const projects = page.locator("aside").getByRole("navigation", { name: "Быстрый переход к проектам" });
    await expect(projects.locator(".brand-projects-mark")).toHaveCSS("background-image", /linear-gradient/);
    await expect(projects.getByTestId("project-icon-image")).toHaveCSS("width", "24px");
    await projects.getByRole("button", { name: "Свернуть список проектов" }).click();
    await expect(projects.getByRole("link", { name: state.projects[0].name, exact: true })).toBeHidden();
    await page.reload();
    await expect(projects.getByRole("button", { name: "Развернуть список проектов" })).toHaveAttribute("aria-expanded", "false");
    await projects.getByRole("button", { name: "Развернуть список проектов" }).click();
    await projects.getByRole("link", { name: state.projects[0].name, exact: true }).click();
    await expect(projects.getByRole("link", { name: state.projects[0].name, exact: true })).toHaveAttribute("aria-current", "page");
    await expect(page.locator(".project-header").getByTestId("project-icon-image")).toHaveCSS("width", "32px");
    await page.getByRole("button", { name: "Свернуть панель", exact: true }).click();
    await projects.getByRole("button", { name: "Свернуть список проектов" }).click();
    await page.getByRole("button", { name: "Развернуть панель", exact: true }).click();
    await expect(projects.getByRole("button", { name: "Развернуть список проектов" })).toHaveAttribute("aria-expanded", "false");
    await page.screenshot({ path: info.outputPath("project-section-collapsed.png") });
    await main.getByRole("link", { name: "Портфель", exact: true }).click();
    await page.getByRole("navigation", { name: "Представления портфеля" }).getByRole("link", { name: "Сводка", exact: true }).click();
    await main.getByRole("link", { name: "Мои задачи", exact: true }).click();
    await main.getByRole("link", { name: "Портфель", exact: true }).click();
    await expect(page.getByRole("region", { name: "Сводка портфеля" })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("region", { name: "Сводка портфеля" })).toBeVisible();
    await page.getByRole("navigation", { name: "Представления портфеля" }).getByRole("link", { name: "Проекты", exact: true }).click();
    await page.goBack();
    await expect(page.getByRole("region", { name: "Сводка портфеля" })).toBeVisible();
    expect(state.errors).toEqual([]);
});

test("мобильный список проектов сворачивается и закрывает меню после перехода", async ({ page }, info) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const state = await installPortfolioApi(page);
    await page.goto("/");
    await page.getByRole("button", { name: "Открыть меню" }).click();
    const menu = page.getByRole("dialog", { name: "Меню навигации" });
    await expect(menu.getByRole("navigation", { name: "Основная навигация" }).getByRole("link", { name: "Проекты", exact: true })).toHaveCount(0);
    const projects = menu.getByRole("navigation", { name: "Быстрый переход к проектам" });
    await expect(projects.getByTestId("project-icon-image")).toHaveCSS("width", "28px");
    await projects.getByRole("button", { name: "Свернуть список проектов" }).click();
    await expect(projects.getByRole("link", { name: state.projects[0].name, exact: true })).toBeHidden();
    await projects.getByRole("button", { name: "Развернуть список проектов" }).click();
    await page.screenshot({ path: info.outputPath("project-menu-mobile.png") });
    await projects.getByRole("link", { name: state.projects[0].name, exact: true }).click();
    await expect(menu).toBeHidden();
    await expect(page.getByRole("heading", { name: state.projects[0].name, exact: true })).toBeVisible();
    expect(state.errors).toEqual([]);
});
