import { expect, test, type Page } from "@playwright/test";
import { installMockApi, mockState } from "./support/calendarMockApi";

test.setTimeout(60_000);

async function dragUnscheduledTask(page: Page) {
    const source = page.getByRole("tabpanel", { name: /^Без срока/ }).getByRole("button", { name: /^Открыть DEMO-1:/ });
    await expect(source).toBeVisible();
    const from = await source.boundingBox();
    const target = await page.locator('[data-date="2026-06-10"]').boundingBox();
    if (!from || !target) throw new Error("Нет задачи без дат или дня для назначения срока");
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
    await page.mouse.down();
    await page.mouse.move(target.x + target.width / 2, target.y + 60, { steps: 12 });
    await page.mouse.up();
}

for (const calendar of ["project", "mine"] as const) {
    test(`${calendar}: первое назначение drag ждёт Save; Cancel не пишет, причина не нужна`, async ({ page }) => {
        const state = mockState();
        state.tasks[0] = { ...state.tasks[0], start_date: null, due_date: null, revision: 7 };
        await installMockApi(page, state);
        const route = calendar === "project" ? "/projects/DEMO/calendar" : "/calendar";
        await page.goto(`${route}?view=grid&scale=week&date=2026-06-10`);
        await page.getByRole("button", { name: calendar === "project" ? "Показать панель" : "Показать задачи без срока", exact: true }).click();
        if (calendar === "project") await page.getByRole("tab", { name: /^Без срока/ }).click();
        await dragUnscheduledTask(page);
        const dialog = page.getByRole("dialog", { name: "Изменить сроки", exact: true });
        await expect(dialog).toBeVisible();
        await expect(dialog.getByLabel("Начало", { exact: true })).toHaveValue("");
        await expect(dialog.getByLabel("Завершение", { exact: true })).toHaveValue("2026-06-10");
        await expect(dialog.getByRole("textbox", { name: /Причина/ })).toHaveCount(0);
        expect(state.writes.filter(write => write.method === "PATCH" || write.path.endsWith("/scenarios/apply"))).toHaveLength(0);
        await dialog.getByRole("button", { name: "Отмена", exact: true }).click();
        await expect(dialog).not.toBeVisible();
        expect(state.tasks[0].start_date).toBeNull();
        expect(state.tasks[0].due_date).toBeNull();
        expect(state.writes.filter(write => write.method === "PATCH" || write.path.endsWith("/scenarios/apply"))).toHaveLength(0);

        await dragUnscheduledTask(page);
        await dialog.getByRole("button", { name: "Сохранить сроки", exact: true }).click();
        await expect(dialog).not.toBeVisible();
        await expect.poll(() => state.writes.filter(write => write.method === "PATCH" && write.path === "/tasks/100"))
            .toEqual([{ method: "PATCH", path: "/tasks/100", body: { start_date: null, due_date: "2026-06-10", expected_revision: 7 } }]);
        expect(state.errors).toEqual([]);
    });
}
