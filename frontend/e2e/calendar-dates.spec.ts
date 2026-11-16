import { expect, test, type Page } from "@playwright/test";
import { installMockApi, mockState, stamp } from "./support/calendarMockApi";

test.setTimeout(60_000);

const originalDate = "2026-06-08";
function datedState() {
    const state = mockState();
    state.tasks[0] = { ...state.tasks[0], start_date: originalDate, due_date: originalDate, revision: 7 };
    return state;
}

async function dragTask(page: Page, view: "grid" | "timeline") {
    const source = view === "grid"
        ? page.getByRole("button", { name: /^DEMO-1:/ })
        : page.getByRole("button", { name: /^Переместить интервал DEMO-1:/ });
    await expect(source).toBeVisible();
    const from = await source.boundingBox();
    if (!from) throw new Error("Нет карточки задачи для переноса");
    const target = view === "grid" ? await page.locator('[data-date="2026-06-10"]').boundingBox() : null;
    const to = target ? { x: target.x + target.width / 2, y: target.y + 45 } : { x: from.x + from.width / 2 + 96, y: from.y + from.height / 2 };
    await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
    await page.mouse.down();
    await page.mouse.move(to.x, to.y, { steps: 12 });
    await page.mouse.up();
}

for (const calendar of ["project", "mine"] as const) {
    for (const view of ["grid", "timeline"] as const) {
        test(`${calendar}/${view}: drag ждёт причины, отмена сохраняет даты, Save отправляет снимок revision`, async ({ page }) => {
            const state = datedState();
            await installMockApi(page, state);
            const route = calendar === "project" ? "/projects/DEMO/calendar" : "/calendar";
            await page.goto(`${route}?view=${view}&scale=${view === "grid" ? "week" : "month"}&date=2026-06-10`);
            await dragTask(page, view);
            const dialog = page.getByRole("dialog", { name: "Изменить сроки", exact: true });
            await expect(dialog).toBeVisible();
            const proposedDue = await dialog.getByLabel("Завершение", { exact: true }).inputValue();
            expect(proposedDue).not.toBe(originalDate);
            expect(state.writes.filter(write => write.method === "PATCH" || write.path.endsWith("/scenarios/apply"))).toHaveLength(0);
            await dialog.getByRole("button", { name: "Отмена", exact: true }).click();
            await expect(dialog).not.toBeVisible();
            expect(state.tasks[0].start_date).toBe(originalDate);
            expect(state.tasks[0].due_date).toBe(originalDate);

            await dragTask(page, view);
            const save = dialog.getByRole("button", { name: "Сохранить сроки", exact: true });
            await dialog.getByRole("textbox", { name: /Причина изменения/ }).fill("   ");
            await expect(save).toBeDisabled();
            await dialog.getByRole("textbox", { name: /Причина изменения/ }).fill("  Согласование графика  ");
            await save.click();
            await expect(dialog).not.toBeVisible();
            await expect.poll(() => state.writes.find(write => write.method === "PATCH" && write.path === "/tasks/100")?.body)
                .toMatchObject({ due_date: proposedDue, date_change_reason: "Согласование графика", expected_revision: 7 });
            expect(state.errors).toEqual([]);
        });
    }
}

test("сценарий применяет одну причину и проверенные версии прямой и зависимой задач", async ({ page }) => {
    const state = datedState();
    state.schedule.cascade = [{ task_id: 104, due_date: "2026-06-20" }];
    await installMockApi(page, state);
    await page.goto("/projects/DEMO/calendar?view=timeline&scale=month&date=2026-06-10");
    await page.getByRole("button", { name: "Что, если", exact: true }).click();
    await page.getByRole("button", { name: "Изменить даты DEMO-1", exact: true }).click();
    const dates = page.getByRole("dialog", { name: "Изменить сроки", exact: true });
    await dates.getByLabel("Завершение", { exact: true }).fill("2026-06-15");
    await expect(dates.getByRole("textbox", { name: /Причина/ })).toHaveCount(0);
    await dates.getByRole("button", { name: "Сохранить сроки", exact: true }).click();
    await expect(dates).not.toBeVisible();
    const apply = page.getByRole("button", { name: "Apply", exact: true });
    await expect(apply).toBeDisabled();
    expect(state.writes.filter(write => write.method === "PATCH" || write.path.endsWith("/scenarios/apply"))).toHaveLength(0);
    await page.getByRole("textbox", { name: /Причина изменения/ }).fill("  Пересмотр договорённостей  ");
    await apply.click();
    await expect.poll(() => state.writes.find(write => write.path.endsWith("/scenarios/apply"))?.body).toEqual({
        date_change_reason: "Пересмотр договорённостей",
        changes: [
            { task_id: 100, start_date: originalDate, due_date: "2026-06-15", expected_updated_at: stamp },
            { task_id: 104, start_date: "2026-06-20", due_date: "2026-06-20", expected_updated_at: stamp },
        ],
    });
    expect(state.errors).toEqual([]);
});
