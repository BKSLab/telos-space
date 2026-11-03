import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { installMockApi, mockState, stamp } from "./support/calendarMockApi";

test.setTimeout(60_000);

/**
 * Шкала проекта: график (docs/calendar.md) — заливка прогресса, режим
 * «Критический путь», связь протягиванием, «сдвинуть и N следующих?»,
 * «Зафиксировать план». SHOTS_DIR=<папка> — сохранить скриншоты.
 */
const SHOTS_DIR = process.env.SHOTS_DIR;
const SCHEDULE_MONTH = "2026-06";
const TIMELINE = `/projects/DEMO/calendar?view=timeline&scale=month&date=${SCHEDULE_MONTH}-15`;

function mockScheduleState() {
    const state = mockState();
    // Все полосы и концы зависимостей внутри явного периода, независимо от даты запуска.
    state.tasks = state.tasks.map((task, index) => ({
        ...task,
        start_date: `${SCHEDULE_MONTH}-${String(index + 1).padStart(2, "0")}`,
        due_date: `${SCHEDULE_MONTH}-${String(index * 2 + 3).padStart(2, "0")}`,
    }));
    return state;
}

async function shot(page: Page, name: string) {
    if (!SHOTS_DIR) return;
    fs.mkdirSync(SHOTS_DIR, { recursive: true });
    // Окно появляется с анимацией — даём ей закончиться.
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(SHOTS_DIR, `${name}.png`) });
}

const bar = (page: Page, taskId: number) => page.locator(`[data-gantt-task-id="${taskId}"]`);

test.describe("шкала проекта: график", () => {
    test("полоса заливается на процент чек-листа, подсказка «Выполнено N%»", async ({ page }) => {
        const state = mockScheduleState();
        state.schedule.progress = { 100: 40, 101: null };
        await installMockApi(page, state);
        await page.goto(TIMELINE);

        await expect(bar(page, 100).locator("[data-bar-progress]")).toHaveAttribute("data-bar-progress", "40");
        const fill = await bar(page, 100).locator("[data-bar-progress]").evaluate((element) => (element as HTMLElement).style.width);
        expect(fill).toBe("40%");
        await expect(bar(page, 100).getByRole("button", { name: /Переместить интервал DEMO-1/ })).toHaveAttribute("title", /Выполнено 40%/);
        // Без чек-листа — без заливки и без строки в подсказке.
        await expect(bar(page, 101).locator("[data-bar-progress]")).toHaveCount(0);
        await expect(bar(page, 101).getByRole("button", { name: /Переместить интервал DEMO-2/ })).not.toHaveAttribute("title", /Выполнено/);
        await shot(page, "progress-1440-light");
        expect(state.errors).toEqual([]);
    });

    test("«Критический путь» подсвечивает цепочку, приглушает остальное и запоминается", async ({ page }) => {
        const state = mockScheduleState();
        state.schedule.critical = [100, 101];
        state.schedule.dependencies = [
            { id: 1, project_id: 1, predecessor_task_id: 100, successor_task_id: 101, dependency_type: "FINISH_TO_START", lag_days: 0, created_at: stamp, is_critical: true },
            { id: 2, project_id: 1, predecessor_task_id: 102, successor_task_id: 103, dependency_type: "FINISH_TO_START", lag_days: 0, created_at: stamp, is_critical: false },
        ];
        await installMockApi(page, state);
        await page.goto(TIMELINE);

        const toggle = page.getByRole("button", { name: "Критический путь" });
        await expect(toggle).toHaveAttribute("aria-pressed", "false");
        await expect(bar(page, 100)).not.toHaveAttribute("data-critical", /./);
        await toggle.click();
        await expect(toggle).toHaveAttribute("aria-pressed", "true");
        await expect(bar(page, 100)).toHaveAttribute("data-critical", "critical");
        await expect(bar(page, 101)).toHaveAttribute("data-critical", "critical");
        await expect(bar(page, 102)).toHaveAttribute("data-critical", "dimmed");
        await expect(page.locator('path[data-critical="critical"]')).toHaveCount(1);
        await expect(page.locator('path[data-critical="dimmed"]')).toHaveCount(1);
        await shot(page, "critical-path-1440-light");

        await page.reload();
        await expect(page.getByRole("button", { name: "Критический путь" })).toHaveAttribute("aria-pressed", "true");
        expect(state.errors).toEqual([]);
    });

    test("ручка на краю полосы: протянул к другой — создана FS-связь, цикл — тост", async ({ page }) => {
        const state = mockScheduleState();
        await installMockApi(page, state);
        await page.goto(TIMELINE);

        const source = bar(page, 100);
        await source.hover();
        const handle = source.getByRole("button", { name: "Связать DEMO-1 со следующей задачей" });
        await expect(handle).toBeVisible();
        await expect(handle).toHaveAttribute("title", /Потяните к другой полосе/);
        const from = await handle.boundingBox();
        const to = await bar(page, 102).boundingBox();
        if (!from || !to) throw new Error("нет полос");
        await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
        await page.mouse.down();
        await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 10 });
        await expect(page.locator("[data-link-preview]")).toBeVisible();
        await shot(page, "link-drag-1440-light");
        await page.mouse.up();

        await expect(page.getByText("Зависимость создана.")).toBeVisible();
        const created = state.writes.find((write) => write.method === "POST" && write.path === "/projects/1/task-dependencies");
        expect(created?.body).toEqual({ predecessor_task_id: 100, successor_task_id: 102, dependency_type: "FINISH_TO_START", lag_days: 0 });

        // Обратная связь создала бы цикл — сервер отвечает 409, пользователь видит причину.
        state.schedule.dependencyError = "Связь создаёт цикл зависимостей.";
        await bar(page, 103).hover();
        const back = bar(page, 103).getByRole("button", { name: "Связать DEMO-4 со следующей задачей" });
        const backFrom = await back.boundingBox();
        const backTo = await bar(page, 104).boundingBox();
        if (!backFrom || !backTo) throw new Error("нет полос");
        await page.mouse.move(backFrom.x + backFrom.width / 2, backFrom.y + backFrom.height / 2);
        await page.mouse.down();
        await page.mouse.move(backTo.x + backTo.width / 2, backTo.y + backTo.height / 2, { steps: 10 });
        await page.mouse.up();
        await expect(page.getByText(/Не удалось создать зависимость: .*цикл/)).toBeVisible();
        expect(state.errors).toEqual([]);
    });

    test("перенос с конфликтом у последователей: «Сдвинуть и N следующих?» — Да / Только эту", async ({ page }) => {
        const state = mockScheduleState();
        // DEMO-2 → DEMO-5: DEMO-5 начинается до срока DEMO-2 + 2 дня.
        state.schedule.dependencies = [
            { id: 1, project_id: 1, predecessor_task_id: 101, successor_task_id: 104, dependency_type: "FINISH_TO_START", lag_days: 0, created_at: stamp },
        ];
        state.schedule.cascade = [{ task_id: 104, due_date: "2099-01-10" }];
        await installMockApi(page, state);
        await page.goto(TIMELINE);

        const endHandle = page.getByRole("button", { name: /Изменить завершение DEMO-2/ });
        await endHandle.focus();
        await endHandle.press("ArrowRight");
        await endHandle.press("ArrowRight");
        const dialog = page.getByRole("dialog", { name: /Сдвинуть и 1 следующую\?/ });
        await expect(dialog).toBeVisible();
        await expect(dialog.getByText("DEMO-5")).toBeVisible();
        await shot(page, "shift-successors-1440-light");

        await dialog.getByRole("button", { name: "Да, сдвинуть" }).click();
        await expect(page.getByText("Перенесено задач: 2.")).toBeVisible();
        const apply = state.writes.find((write) => write.path === "/projects/1/calendar/scenarios/apply");
        expect((apply?.body as { changes: { task_id: number }[] }).changes.map((change) => change.task_id)).toEqual([101, 104]);
        expect(state.writes.some((write) => write.method === "PATCH" && write.path === "/tasks/101")).toBe(false);

        // «Только эту» — обычное сохранение одной задачи, без сценария.
        await endHandle.focus();
        await endHandle.press("ArrowRight");
        await page.getByRole("dialog", { name: /Сдвинуть и 1 следующую\?/ }).getByRole("button", { name: "Только эту" }).click();
        await expect.poll(() => state.writes.some((write) => write.method === "PATCH" && write.path === "/tasks/101")).toBe(true);
        expect(state.writes.filter((write) => write.path === "/projects/1/calendar/scenarios/apply")).toHaveLength(1);
        expect(state.errors).toEqual([]);
    });

    test("«Зафиксировать план»: подтверждение с числом задач, затем один пакетный запрос", async ({ page }) => {
        const state = mockScheduleState();
        await installMockApi(page, state);
        await page.goto(TIMELINE);

        const button = page.getByRole("button", { name: "Зафиксировать план" });
        await expect(button).toHaveAttribute("title", /Перед записью спросим подтверждение/);
        const messages: string[] = [];
        page.once("dialog", async (dialog) => {
            messages.push(dialog.message());
            await dialog.dismiss();
        });
        await button.click();
        await expect.poll(() => messages.length).toBe(1);
        expect(messages[0]).toContain("перезапишет план (baseline) у 5 задач из 12");
        // Отказ — ничего не записано, кроме подсчёта dry_run.
        expect(state.writes.filter((write) => write.path === "/projects/1/baseline")).toHaveLength(1);

        page.once("dialog", (dialog) => dialog.accept());
        await button.click();
        await expect(page.getByText("План проекта зафиксирован: обновлено 5 задач.")).toBeVisible();
        expect(state.writes.filter((write) => write.path === "/projects/1/baseline")).toHaveLength(3);
        expect(state.errors).toEqual([]);
    });

});
