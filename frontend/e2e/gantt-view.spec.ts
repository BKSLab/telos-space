import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { installMockApi, mockState } from "./support/calendarMockApi";

test.setTimeout(60_000);

/**
 * Гант рядом с календарём и выгрузка (docs/calendar.md, «Рядом», «Шкала», «Выгрузка»):
 * вид «Рядом» на широком экране, разделитель, цвет полос, колонки, «Уместить проект»,
 * PNG/PDF, режим показа. SHOTS_DIR=<папка> — скриншоты светлой и тёмной темы.
 */
const SHOTS_DIR = process.env.SHOTS_DIR;
const iso = (offset: number) => {
    const date = new Date();
    date.setDate(date.getDate() + offset);
    return date.toISOString().slice(0, 10);
};

async function shot(page: Page, name: string) {
    if (!SHOTS_DIR) return;
    fs.mkdirSync(SHOTS_DIR, { recursive: true });
    await page.waitForTimeout(200);
    await page.screenshot({ path: path.join(SHOTS_DIR, `${name}.png`) });
}

async function expectNoPageScroll(page: Page) {
    const metrics = await page.evaluate(() => ({
        scroll: document.documentElement.scrollHeight,
        client: document.documentElement.clientHeight,
        scrollW: document.documentElement.scrollWidth,
        clientW: document.documentElement.clientWidth,
    }));
    expect(metrics.scroll, "страница не прокручивается").toBeLessThanOrEqual(metrics.client);
    expect(metrics.scrollW, "нет прокрутки вбок").toBeLessThanOrEqual(metrics.clientW);
}

test.describe("Гант: вид «Рядом»", () => {
    test("есть на 1440, сетка сверху, шкала снизу, разделитель тянется и запоминается", async ({ page }) => {
        const state = mockState();
        await installMockApi(page, state);
        await page.goto(`/projects/DEMO/calendar?view=grid&scale=month&date=${iso(0)}`);
        const viewGroup = page.getByRole("group", { name: "Вид календаря" });
        await viewGroup.getByRole("button", { name: /Рядом/ }).click();
        await expect(page).toHaveURL(/view=split/);
        await expect(page.getByRole("grid", { name: "Месяц" })).toBeVisible();
        await expect(page.locator("[data-split-pane='timeline'] [data-timeline-scroll]")).toBeVisible();
        await expect(page.getByRole("note", { name: "Легенда шкалы" })).toBeVisible();

        const gridPane = page.locator("[data-split-pane='grid']");
        const before = (await gridPane.boundingBox())?.height ?? 0;
        const separator = page.getByRole("separator", { name: "Граница между сеткой и шкалой" });
        const box = await separator.boundingBox();
        if (!box) throw new Error("нет разделителя");
        await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
        await page.mouse.down();
        await page.mouse.move(box.x + box.width / 2, box.y - 120, { steps: 6 });
        await page.mouse.up();
        const after = (await gridPane.boundingBox())?.height ?? 0;
        expect(after).toBeLessThan(before - 80);
        const stored = await page.evaluate(() => Number(window.localStorage.getItem("telos.calendar.project.split")));
        expect(stored).toBeGreaterThan(0.2);
        expect(stored).toBeLessThan(0.5);
        await expectNoPageScroll(page);
        await shot(page, "gantt-split-1440-light");
        expect(state.errors).toEqual([]);
    });

    test("на 1024 варианта нет, ссылка ?view=split открывает сетку", async ({ page }) => {
        await page.setViewportSize({ width: 1024, height: 800 });
        const state = mockState();
        await installMockApi(page, state);
        await page.goto(`/projects/DEMO/calendar?view=split&scale=month&date=${iso(0)}`);
        await expect(page.getByRole("grid", { name: "Месяц" })).toBeVisible();
        await expect(page.getByRole("group", { name: "Вид календаря" }).getByRole("button", { name: /Рядом/ })).toHaveCount(0);
        await expect(page.locator("[data-calendar-split]")).toHaveCount(0);
        await expectNoPageScroll(page);
    });

    test("клик по дню в сетке прокручивает шкалу к этому дню", async ({ page }) => {
        const state = mockState();
        await installMockApi(page, state);
        await page.goto(`/projects/DEMO/calendar?view=split&scale=month&tscale=quarter&date=${iso(0)}`);
        const scroller = page.locator("[data-split-pane='timeline'] [data-timeline-scroll]");
        await expect(scroller).toBeVisible();
        const start = await scroller.evaluate((element) => element.scrollLeft);
        // Последний день месяца сетки — шкала уезжает вправо.
        const lastDay = page.getByRole("grid", { name: "Месяц" }).getByRole("button", { name: /^Открыть день/ }).last();
        await lastDay.click();
        await expect(page).toHaveURL(/view=split/);
        await expect.poll(() => scroller.evaluate((element) => element.scrollLeft)).not.toBe(start);
    });
});

test.describe("Гант: шкала", () => {
    test("цвет полос по «Цвет по», скобка развёрнутой стадии, инициалы, колонки, ширина", async ({ page }) => {
        const state = mockState();
        await installMockApi(page, state);
        await page.goto(`/projects/DEMO/calendar?view=timeline&scale=month&date=${iso(0)}`);
        const bar = page.locator("[data-bar-color]").first();
        await expect(bar).toBeVisible();
        const statusColor = await bar.getAttribute("data-bar-color");
        await page.getByLabel("Цвет карточек по").selectOption("stage");
        await expect.poll(() => bar.getAttribute("data-bar-color")).not.toBe(statusColor);
        await expect(page.getByRole("note", { name: "Легенда шкалы" })).toContainText("Цвет по этапу");
        await expect(page.locator("[data-stage-bracket]").first()).toBeVisible();
        await expect(page.locator("[data-bar-initials]").first()).toBeVisible();

        await page.getByRole("button", { name: "Колонки шкалы" }).click();
        await page.getByRole("dialog", { name: "Колонки шкалы" }).getByLabel("Срок").check();
        await page.keyboard.press("Escape");
        await expect(page.locator("[data-gantt-cell='due']").first()).toBeVisible();
        expect(await page.evaluate(() => window.localStorage.getItem("telos.calendar.project.gantt.columns"))).toBe('["due"]');

        const handle = page.locator("[data-label-resize]");
        const box = await handle.boundingBox();
        if (!box) throw new Error("нет ручки ширины");
        await page.mouse.move(box.x + 2, box.y + box.height / 2);
        await page.mouse.down();
        await page.mouse.move(box.x + 122, box.y + box.height / 2, { steps: 6 });
        await page.mouse.up();
        expect(await page.evaluate(() => Number(window.localStorage.getItem("telos.calendar.project.gantt.labelWidth")))).toBe(330);
        await expectNoPageScroll(page);
        expect(state.errors).toEqual([]);
    });

    test("«Уместить проект» и Ctrl+колесо меняют масштаб", async ({ page }) => {
        const state = mockState();
        await installMockApi(page, state);
        await page.goto(`/projects/DEMO/calendar?view=timeline&scale=week&date=${iso(0)}`);
        await page.getByRole("button", { name: "Уместить проект" }).click();
        await expect(page).toHaveURL(/fit=1/);
        await expect(page.getByRole("heading", { name: /Весь срок проекта/ })).toBeVisible();
        const scroller = page.locator("[data-timeline-scroll]");
        const box = await scroller.boundingBox();
        if (!box) throw new Error("нет шкалы");
        await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
        const scaleBefore = new URL(page.url()).searchParams.get("scale");
        await page.keyboard.down("Control");
        // Весь срок демо-проекта умещается только в «Год» — колесо вверх делает крупнее.
        await page.mouse.wheel(0, -200);
        await page.keyboard.up("Control");
        await expect.poll(() => new URL(page.url()).searchParams.get("scale")).not.toBe(scaleBefore);
        await expect(page).toHaveURL(/fit=1/);
        await page.getByRole("button", { name: "Уместить проект" }).click();
        await expect(page).not.toHaveURL(/fit=1/);
    });
});

test.describe("Гант: выгрузка и показ", () => {
    test("«Скачать» → PNG отдаёт картинку, PDF — документ", async ({ page }) => {
        const state = mockState();
        await installMockApi(page, state);
        await page.goto(`/projects/DEMO/calendar?view=timeline&scale=month&date=${iso(0)}`);
        await expect(page.locator("[data-timeline-scroll]")).toBeVisible();

        await page.getByRole("button", { name: "Скачать Гант" }).click();
        const pngPromise = page.waitForEvent("download");
        await page.getByRole("dialog", { name: "Скачать Гант" }).getByRole("button", { name: "PNG" }).click();
        const png = await pngPromise;
        expect(png.suggestedFilename()).toMatch(/^DEMO-гант-\d{4}-\d{2}-\d{2}\.png$/);
        const pngBytes = fs.readFileSync(await png.path());
        expect(pngBytes.subarray(1, 4).toString()).toBe("PNG");
        expect(pngBytes.length).toBeGreaterThan(10_000);
        if (SHOTS_DIR) {
            fs.mkdirSync(SHOTS_DIR, { recursive: true });
            fs.writeFileSync(path.join(SHOTS_DIR, "gantt-export.png"), pngBytes);
        }

        await expect(page.getByRole("button", { name: "Скачать Гант" })).toBeEnabled();
        await page.getByRole("button", { name: "Скачать Гант" }).click();
        const pdfPromise = page.waitForEvent("download");
        await page.getByRole("dialog", { name: "Скачать Гант" }).getByRole("button", { name: "PDF" }).click();
        const pdf = await pdfPromise;
        expect(pdf.suggestedFilename()).toMatch(/\.pdf$/);
        expect(fs.readFileSync(await pdf.path()).subarray(0, 5).toString()).toBe("%PDF-");
        expect(state.errors).toEqual([]);
    });

    test("режим показа: весь срок, шапка с проектом и датой, легенда, без ручек и кнопок", async ({ page }) => {
        const state = mockState();
        await installMockApi(page, state);
        await page.goto(`/projects/DEMO/calendar?view=timeline&scale=month&date=${iso(0)}`);
        await page.getByRole("button", { name: "Скачать Гант" }).click();
        await page.getByRole("dialog", { name: "Скачать Гант" }).getByRole("button", { name: "Режим показа" }).click();
        await expect(page).toHaveURL(/present=1/);
        const present = page.locator("[data-gantt-present]");
        await expect(present.getByRole("heading", { name: "Запуск портала заказов" })).toBeVisible();
        await expect(present.getByText(/по состоянию на/)).toBeVisible();
        await expect(present.getByRole("note", { name: "Легенда шкалы" })).toBeVisible();
        await expect(present.locator("[data-bar-color]").first()).toBeVisible();
        await expect(present.getByRole("button", { name: /^Изменить (начало|завершение|даты)/ })).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Сегодня" })).toHaveCount(0);
        await expectNoPageScroll(page);
        await page.keyboard.press("Escape");
        await expect(page).not.toHaveURL(/present=1/);
        await expect(page.getByRole("button", { name: "Сегодня" })).toBeVisible();
    });
});

test.describe("Гант: скриншоты тем", () => {
    for (const theme of ["light", "dark"] as const) {
        test(`рядом, шкала и показ — ${theme}`, async ({ page }) => {
            test.skip(!SHOTS_DIR, "скриншоты только с SHOTS_DIR");
            const state = mockState();
            await installMockApi(page, state);
            await page.emulateMedia({ colorScheme: theme });
            await page.goto(`/projects/DEMO/calendar?view=split&scale=month&tscale=month&date=${iso(0)}`);
            await expect(page.locator("[data-calendar-split]")).toBeVisible();
            await shot(page, `gantt-split-${theme}`);
            await page.goto(`/projects/DEMO/calendar?view=timeline&scale=month&date=${iso(0)}`);
            await page.getByLabel("Цвет карточек по").selectOption("stage");
            await expect(page.locator("[data-bar-color]").first()).toBeVisible();
            await shot(page, `ganariseline-stage-${theme}`);
            await page.goto(`/projects/DEMO/calendar?view=timeline&scale=month&present=1&date=${iso(0)}`);
            await expect(page.locator("[data-gantt-sheet]")).toBeVisible();
            await shot(page, `gantt-present-${theme}`);
        });
    }
});
