import { expect, test, type Page } from "@playwright/test";
import { installMockApi, mockState } from "./support/calendarMockApi";

test.setTimeout(60_000);

const iso = (offset = 0) => {
    const date = new Date();
    date.setDate(date.getDate() + offset);
    return date.toISOString().slice(0, 10);
};

async function noOuterOverflow(page: Page) {
    const size = await page.evaluate(() => ({
        width: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth,
        height: document.documentElement.clientHeight, scrollHeight: document.documentElement.scrollHeight,
    }));
    expect(size.scrollWidth).toBeLessThanOrEqual(size.width + 1);
    expect(size.scrollHeight).toBeLessThanOrEqual(size.height + 1);
}

for (const viewport of [{ width: 320, height: 740 }, { width: 390, height: 844 }, { width: 768, height: 1024 }, { width: 844, height: 390 }]) {
    test.describe(`календарь ${viewport.width}×${viewport.height}`, () => {
        test.use({ viewport, isMobile: true, hasTouch: true });

        test("шкала, выбор дат без drag, панель и смена периода", async ({ page }) => {
            const state = mockState();
            await installMockApi(page, state);
            await page.goto(`/projects/DEMO/calendar?view=timeline&scale=month&date=${iso()}`);
            await expect(page.getByRole("combobox", { name: "Вид календаря" })).toHaveValue("timeline");
            const scroller = page.locator("[data-timeline-scroll]");
            await expect(scroller).toBeVisible();
            expect((await scroller.boundingBox())?.height ?? 0).toBeGreaterThan(40);
            await page.getByRole("button", { name: "Изменить даты DEMO-1", exact: true }).click();
            const dates = page.getByRole("dialog", { name: "Срок DEMO-1", exact: true });
            await dates.getByLabel("Завершение", { exact: true }).fill(iso(9));
            await dates.getByRole("button", { name: "Сохранить", exact: true }).click();
            await expect.poll(() => state.writes.find((write) => write.method === "PATCH" && write.path === "/tasks/100")?.body).toMatchObject({ due_date: iso(9) });
            await expect(dates).not.toBeVisible();

            await page.getByRole("button", { name: "Инструменты календаря", exact: true }).click();
            await page.getByRole("button", { name: "Показать панель", exact: true }).click();
            const panel = page.getByRole("dialog", { name: "Панель календаря", exact: true });
            await expect(panel).toBeVisible();
            await page.keyboard.press("Tab");
            await expect.poll(() => panel.evaluate(element => element.contains(document.activeElement))).toBe(true);
            await panel.getByRole("tab", { name: "Без срока", exact: true }).click();
            await expect(panel.getByRole("tabpanel", { name: "Без срока", exact: true })).toContainText("У всех задач есть срок");
            await panel.getByRole("button", { name: "Свернуть панель", exact: true }).click();
            await expect(panel).not.toBeVisible();

            await page.getByRole("combobox", { name: "Вид календаря" }).selectOption("grid");
            await page.getByRole("combobox", { name: "Масштаб календаря" }).selectOption("week");
            await expect(page.locator('[data-calendar-grid="week"]')).toBeVisible();
            const before = new URL(page.url()).searchParams.get("date");
            await page.getByRole("button", { name: "Следующий период", exact: true }).click();
            await expect.poll(() => new URL(page.url()).searchParams.get("date")).not.toBe(before);
            await noOuterOverflow(page);
            expect(state.errors).toEqual([]);
        });

        test("личные задачи видны в месяце, фильтры и портфель доступны", async ({ page }) => {
            const state = mockState();
            await installMockApi(page, state);
            await page.goto(`/calendar?view=grid&scale=month&date=${iso()}`);
            await expect(page.getByText("Личная подготовка к поездке", { exact: true })).toBeVisible();
            await expect(page.getByRole("button", { name: "Изменить даты T-1", exact: true })).toBeVisible();
            await page.getByRole("button", { name: "Инструменты календаря", exact: true }).click();
            await page.getByRole("button", { name: "Фильтры календаря", exact: true }).click();
            await page.getByRole("combobox", { name: "Фильтр по приоритету" }).selectOption("HIGH");
            await expect(page).toHaveURL(/priority=HIGH/);
            await page.keyboard.press("Escape");
            await page.getByRole("combobox", { name: "Вид календаря" }).selectOption("timeline");
            await expect(page.locator("[data-timeline-scroll]")).toBeVisible();
            await page.getByRole("tab", { name: "Проекты", exact: true }).click();
            await expect(page.getByRole("button", { name: /Запуск портала заказов: выполнено 25%/ })).toBeVisible();
            await noOuterOverflow(page);
            expect(state.errors).toEqual([]);
        });
    });
}

test.describe("мобильные инструменты Ганта", () => {
    test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

    test("загрузка раскрывается по нажатию, дополнительные колонки прокручиваются", async ({ page }) => {
        const state = mockState();
        await installMockApi(page, state);
        await page.goto(`/projects/DEMO/calendar?view=timeline&scale=month&date=${iso()}&group=assignee`);
        const scroll = page.locator("[data-timeline-scroll]");
        await expect(page.getByRole("list", { name: "Загрузка: Иванова Анна", exact: true })).toBeVisible();
        await scroll.evaluate((element) => { element.scrollLeft = 0; element.scrollTop = 0; });
        await page.getByRole("button", { name: /^Показать загрузку Иванова Анна на/ }).first().click();
        const details = page.getByRole("dialog", { name: "Загрузка: Иванова Анна", exact: true });
        await expect(details).toContainText(/задач/);
        await details.getByRole("button", { name: "Закрыть", exact: true }).last().click();
        await page.getByRole("button", { name: "Инструменты календаря", exact: true }).click();
        await page.getByRole("button", { name: "Колонки шкалы", exact: true }).click();
        await page.getByRole("checkbox", { name: "Срок", exact: true }).check();
        await page.keyboard.press("Escape");
        await expect(page.locator('[data-gantt-cell="due"]')).toHaveCount(state.tasks.length);
        await scroll.evaluate((element) => { element.scrollLeft = element.scrollWidth; });
        expect(await scroll.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
        await noOuterOverflow(page);
        expect(state.errors).toEqual([]);
    });

    test("панель сценария закрывается без потери изменений и снова показывает расчёт", async ({ page }) => {
        const state = mockState();
        await installMockApi(page, state);
        await page.goto(`/projects/DEMO/calendar?view=timeline&scale=month&date=${iso()}`);
        await page.getByRole("button", { name: "Инструменты календаря", exact: true }).click();
        await page.getByRole("button", { name: "Что, если", exact: true }).click();
        const panel = page.getByRole("dialog", { name: "Панель календаря", exact: true });
        await expect(panel).toContainText("Изменения ещё не сохранены");
        await panel.getByRole("button", { name: "Свернуть панель", exact: true }).click();
        await page.getByRole("button", { name: "Изменить даты DEMO-1", exact: true }).click();
        const dates = page.getByRole("dialog", { name: "Срок DEMO-1", exact: true });
        await dates.getByLabel("Завершение", { exact: true }).fill(iso(6));
        await dates.getByRole("button", { name: "Сохранить", exact: true }).click();
        await expect(panel).toBeVisible();
        await expect(panel.getByRole("button", { name: "Apply", exact: true })).toBeEnabled();
        expect(state.writes.some((write) => write.path.endsWith("/calendar/scenarios/preview"))).toBe(true);
        expect(state.writes.some((write) => write.method === "PATCH")).toBe(false);
        await panel.getByRole("button", { name: "Отменить", exact: true }).click();
        expect(state.errors).toEqual([]);
    });
});
