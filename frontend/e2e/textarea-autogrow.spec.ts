import { expect, test, type Locator, type Page } from "@playwright/test";
import { installMockApi, mockState } from "./support/taskMockApi";

declare global {
    interface Window { textareaResizeErrors?: string[] }
}

test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
        window.textareaResizeErrors = [];
        window.addEventListener("error", event => {
            if (event.message.includes("ResizeObserver")) window.textareaResizeErrors?.push(event.message);
        });
    });
});

test.afterEach(async ({ page }) => {
    expect(await page.evaluate(() => window.textareaResizeErrors ?? [])).toEqual([]);
});

const lines = Array.from({ length: 7 }, (_, index) => `Строка ${index + 1}: описание проблемы и ожидаемого результата.`).join("\n");
const headers = {
    "access-control-allow-origin": "http://127.0.0.1:4179",
    "access-control-allow-credentials": "true",
    "access-control-allow-headers": "content-type",
    "access-control-allow-methods": "GET, POST, PATCH, DELETE, OPTIONS",
};
const metrics = (field: Locator) => field.evaluate(element => ({
    height: element.getBoundingClientRect().height,
    client: element.clientHeight,
    scroll: element.scrollHeight,
    overflow: getComputedStyle(element).overflowY,
}));

async function installForms(page: Page, additional = "") {
    const state = mockState();
    state.projects[0].description_md = additional;
    await installMockApi(page, state);
    await page.route("**/api/v1/projects/defaults", route => route.fulfill({
        json: { stages: [{ name: "В работе", color: "#58a6ff", is_done_stage: false }] },
        headers,
    }));
    return state;
}

for (const viewport of [{ name: "desktop", width: 1440, height: 1000 }, { name: "mobile", width: 390, height: 844 }]) {
    test(`паспорт проекта: поля растут и уменьшаются — ${viewport.name}`, async ({ page }, testInfo) => {
        await page.setViewportSize(viewport);
        const state = await installForms(page);
        await page.goto("/projects/new");
        const problem = page.getByLabel("Проблема", { exact: true });
        await expect(problem).toBeVisible();
        const initial = await metrics(problem);
        for (const label of ["Проблема", "Цель", "Ожидаемый результат", "Дополнительно"]) {
            const field = page.getByLabel(label, { exact: true });
            await field.fill(lines);
            await expect.poll(async () => (await metrics(field)).height).toBeGreaterThan(initial.height + 50);
            const size = await metrics(field);
            expect(size.scroll).toBeLessThanOrEqual(size.client + 1);
            await expect(field).toHaveValue(lines);
        }
        await problem.scrollIntoViewIfNeeded();
        await page.screenshot({ path: testInfo.outputPath(`autogrow-project-${viewport.name}.png`) });
        await problem.fill("Короткое описание");
        await expect.poll(async () => (await metrics(problem)).height).toBeLessThanOrEqual(initial.height + 1);
        await problem.fill("");
        await expect.poll(async () => (await metrics(problem)).height).toBeGreaterThanOrEqual(initial.height - 1);
        expect(state.errors).toEqual([]);
    });
}

test("готовый текст при открытии настроек виден без ручного растягивания", async ({ page }) => {
    const content = `${lines}\n${lines}`;
    const state = await installForms(page, content);
    await page.goto("/projects/DEMO/settings");
    const additional = page.getByLabel("Дополнительно", { exact: true });
    await expect(additional).toHaveValue(content);
    await expect.poll(async () => (await metrics(additional)).height).toBeGreaterThan(250);
    const size = await metrics(additional);
    expect(size.scroll).toBeLessThanOrEqual(size.client + 1);
    await additional.fill("Кратко");
    await expect.poll(async () => (await metrics(additional)).height).toBeLessThan(100);
    expect(state.errors).toEqual([]);
});

test("перенос строк пересчитывается при смене ширины контейнера и экрана", async ({ page }) => {
    const state = await installForms(page);
    await page.goto("/projects/new");
    const field = page.getByLabel("Проблема", { exact: true });
    await field.fill("Нужно сохранить весь контекст проекта и удобно читать длинное описание. ".repeat(4));
    const initial = await metrics(field);
    await field.evaluate(element => { element.parentElement!.style.maxWidth = "260px"; });
    await expect.poll(async () => (await metrics(field)).height).toBeGreaterThan(initial.height + 50);
    await field.evaluate(element => { element.parentElement!.style.removeProperty("max-width"); });
    await expect.poll(async () => (await metrics(field)).height).toBeLessThanOrEqual(initial.height + 1);
    await page.setViewportSize({ width: 390, height: 844 });
    await expect.poll(async () => (await metrics(field)).height).toBeGreaterThan(initial.height + 50);
    const size = await metrics(field);
    expect(size.scroll).toBeLessThanOrEqual(size.client + 1);
    expect(state.errors).toEqual([]);
});

test("очень длинное описание в мобильном диалоге прокручивается и оставляет действия доступными", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const state = await installForms(page);
    await page.goto("/my-tasks");
    await page.getByRole("button", { name: "Новая задача", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Новая задача", exact: true });
    const field = dialog.getByLabel("Описание", { exact: true });
    const initial = await metrics(field);
    await field.fill(`${lines}\n`.repeat(15));
    await expect.poll(async () => (await metrics(field)).scroll - (await metrics(field)).client).toBeGreaterThan(100);
    expect((await metrics(field)).height).toBeLessThanOrEqual(844 * 0.6 + 1);
    expect((await metrics(field)).overflow).toBe("auto");
    await field.press("Control+End");
    await expect.poll(() => field.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
    await expect(dialog.getByRole("button", { name: "Создать задачу", exact: true })).toBeVisible();
    await page.setViewportSize({ width: 390, height: 400 });
    await expect.poll(async () => (await metrics(field)).height).toBeLessThanOrEqual(240 + 1);
    await expect(dialog.getByRole("button", { name: "Создать задачу", exact: true })).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await field.fill("Одна строка");
    await expect.poll(async () => (await metrics(field)).height).toBeLessThanOrEqual(initial.height + 1);
    await page.screenshot({ path: testInfo.outputPath("autogrow-task-mobile.png") });
    expect(state.errors).toEqual([]);
});

test("редактор документа с большим rows ограничивается высотой небольшого экрана", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 600 });
    const state = await installForms(page);
    const document = { id: 5, project_id: 1, slug: "notes", title: "Заметки", content_md: `${lines}\n`.repeat(10), created_at: "2026-11-06T10:00:00Z", updated_at: "2026-11-06T10:00:00Z" };
    await page.route("**/api/v1/projects/1/documents", route => route.fulfill({ json: [document], headers }));
    await page.route("**/api/v1/documents/5", route => route.fulfill({ json: document, headers }));
    await page.goto("/projects/DEMO/docs/notes");
    await page.getByRole("button", { name: "Редактировать документ", exact: true }).click();
    const field = page.getByLabel("Содержимое документа в Markdown", { exact: true });
    await expect(field).toHaveValue(document.content_md);
    await expect.poll(async () => (await metrics(field)).height).toBeLessThanOrEqual(361);
    const size = await metrics(field);
    expect(size.scroll).toBeGreaterThan(size.client);
    expect(size.overflow).toBe("auto");
    await page.getByRole("button", { name: "Сохранить", exact: true }).scrollIntoViewIfNeeded();
    await expect(page.getByRole("button", { name: "Сохранить", exact: true })).toBeVisible();
    expect(state.errors).toEqual([]);
});
