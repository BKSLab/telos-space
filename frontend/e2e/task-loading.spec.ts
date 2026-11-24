import { expect, test } from "@playwright/test";
import { installMockApi, mockState } from "./support/taskMockApi";

for (const width of [1440, 320]) {
    test(`kanban copy is beside the key and does not open or drag at ${width}px`, async ({ page }, info) => {
        await page.setViewportSize({ width, height: 1000 });
        await page.addInitScript(() => {
            Object.defineProperty(navigator, "clipboard", { value: { writeText: async (text: string) => { (window as unknown as { copied: string }).copied = text; } } });
        });
        const state = mockState();
        await installMockApi(page, state);
        await page.goto("/projects/DEMO/tasks");
        const task = state.tasks[0];
        const card = page.getByRole("heading", { name: task.title, exact: true }).locator("../..");
        const key = card.getByRole("button", { name: task.key, exact: true });
        const copy = card.getByRole("button", { name: "Скопировать ключ и название" });
        await expect(copy).toBeVisible();
        const keyBox = (await key.boundingBox())!;
        const copyBox = (await copy.boundingBox())!;
        expect(copyBox.x).toBeGreaterThanOrEqual(keyBox.x + keyBox.width);
        expect(Math.abs(copyBox.y - keyBox.y)).toBeLessThan(12);
        await copy.click();
        await expect(card.getByRole("status")).toHaveText("Ключ и название скопированы");
        expect(await page.evaluate(() => (window as unknown as { copied: string }).copied)).toBe(`${task.key} ${task.title}`);
        await expect(page.getByRole("dialog")).toHaveCount(0);
        await expect(page.locator(".brand-kanban-dragging")).toHaveCount(0);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: info.outputPath(`kanban-copy-${width}.png`) });
    });
}

test("returning to tasks uses fresh cached list without another download", async ({ page }) => {
    await installMockApi(page, mockState());
    let reads = 0;
    page.on("request", request => { if (request.method() === "GET" && new URL(request.url()).pathname === "/api/v1/projects/1/tasks") reads++; });
    await page.goto("/projects/DEMO/tasks");
    await expect(page.getByRole("heading", { name: "Согласовать макеты личного кабинета", exact: true })).toBeVisible();
    const initialReads = reads;
    await page.getByRole("link", { name: "Документы", exact: true }).first().click();
    await expect(page).toHaveURL(/\/docs/);
    await page.getByRole("link", { name: "Задачи", exact: true }).first().click();
    await expect(page.getByRole("heading", { name: "Согласовать макеты личного кабинета", exact: true })).toBeVisible();
    expect(reads).toBe(initialReads);
});
