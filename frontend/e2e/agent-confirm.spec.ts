import { expect, test, type Page, type Route } from "@playwright/test";
import fs from "node:fs";
import { installMockApi, mockState } from "./support/mockApi";
import type { AgentPendingAction } from "../src/lib/types";

/**
 * Подтверждение действия агента по ссылке `/?confirm=<id>` (#40793, личный трекер).
 * CONFIRM_SHOTS=<папка> — дополнительно снять окно в светлой и тёмной теме и в личном трекере.
 */
const SHOTS = process.env.CONFIRM_SHOTS;
const ID = "0f1e2d3c-0000-4000-8000-000000000001";

test.use({ launchOptions: { args: ["--disable-features=LocalNetworkAccessChecks"] } });
test.setTimeout(60_000);

const ACTION: AgentPendingAction = {
    id: ID,
    tool_name: "telos_delete_task",
    title: "Удалить задачу",
    status: "pending",
    arguments: { task_id: 100, expected_updated_at: "2026-09-30T10:00:00Z" },
    preview: { task: { id: 100, key: "DEMO-1", title: "Согласовать макеты личного кабинета", due_date: "2026-10-03" } },
    result: {},
    project: { id: 1, key: "DEMO", name: "Запуск портала заказов" },
    created_at: "2026-09-30T10:00:00Z",
    expires_at: "2026-10-01T10:00:00Z",
};

/** Бэкенд подтверждения: состояние живёт в замыкании, решения пишутся в журнал. */
async function installPending(page: Page, initial: AgentPendingAction | null = ACTION) {
    const log: { method: string; path: string; body: string | null }[] = [];
    let current = initial;
    await page.route("**/api/v1/agent/pending/**", async (route: Route) => {
        const request = route.request();
        const path = new URL(request.url()).pathname;
        const headers = { "access-control-allow-origin": request.headers()["origin"] ?? "http://127.0.0.1:4179", "access-control-allow-credentials": "true", "access-control-allow-headers": "content-type", "access-control-allow-methods": "GET, POST, OPTIONS" };
        if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
        const reply = (json: unknown, status = 200) => route.fulfill({ json, status, headers });
        log.push({ method: request.method(), path, body: request.postData() });
        if (!current) return reply({ detail: "Объект или действие недоступны в этом проекте." }, 404);
        if (request.method() === "GET") return reply(current);
        if (current.status !== "pending") return reply({ detail: "По этому действию решение уже принято." }, 409);
        current = path.endsWith("/confirm")
            ? { ...current, status: "completed", preview: {}, result: { deleted: true, participant_decision: "approve" } }
            : { ...current, status: "rejected", preview: {}, result: { message: "Участник отклонил действие. Изменения не применены.", participant_decision: "reject" } };
        return reply(current);
    });
    return log;
}

for (const theme of ["light", "dark"] as const) {
    test(`ссылка ?confirm= открывает окно, подтверждение выполняет сохранённое действие (${theme})`, async ({ page }) => {
        await page.addInitScript((value) => localStorage.setItem("bsd_theme", value), theme);
        const state = mockState();
        await installMockApi(page, state);
        const log = await installPending(page);
        await page.goto(`/?confirm=${ID}`);

        const dialog = page.getByRole("dialog", { name: "Подтверждение действия агента" });
        await expect(dialog).toBeVisible();
        await expect(dialog.getByText("Проект «Запуск портала заказов» (DEMO)")).toBeVisible();
        await expect(dialog.getByText("Удалить задачу", { exact: true })).toBeVisible();
        await expect(dialog.getByText("Согласовать макеты личного кабинета")).toBeVisible();
        await expect(dialog.getByText("Ожидает вашего решения")).toBeVisible();
        const confirm = dialog.getByRole("button", { name: "Подтвердить" });
        await expect(confirm).toHaveAttribute("title", /ровно с этими параметрами/);
        await expect(dialog.getByRole("button", { name: "Отклонить" })).toHaveAttribute("title", /не изменятся/);
        await page.waitForTimeout(300);
        if (SHOTS) {
            fs.mkdirSync(SHOTS, { recursive: true });
            await page.screenshot({ path: `${SHOTS}/confirm-${theme}.png` });
        }

        await confirm.click();
        await expect(dialog.getByText("Выполнено", { exact: true })).toBeVisible();
        await expect(dialog.getByRole("button", { name: "Подтвердить" })).toHaveCount(0);
        const decision = log.find((entry) => entry.method === "POST");
        expect(decision).toEqual({ method: "POST", path: `/api/v1/agent/pending/${ID}/confirm`, body: null });
        if (SHOTS) await page.screenshot({ path: `${SHOTS}/confirm-done-${theme}.png` });

        await dialog.getByRole("button", { name: "Закрыть" }).last().click();
        await expect(page.getByRole("dialog")).toHaveCount(0);
        expect(new URL(page.url()).searchParams.has("confirm")).toBe(false);
        expect(state.errors).toEqual([]);
    });
}

test("отклонение ничего не меняет, чужая ссылка — понятный отказ", async ({ page }) => {
    await installMockApi(page, mockState());
    const log = await installPending(page);
    await page.goto(`/my-tasks?confirm=${ID}`);
    const dialog = page.getByRole("dialog", { name: "Подтверждение действия агента" });
    await dialog.getByRole("button", { name: "Отклонить" }).click();
    await expect(dialog.getByText("Отклонено — ничего не изменилось")).toBeVisible();
    await expect(dialog.getByText("Участник отклонил действие. Изменения не применены.")).toBeVisible();
    expect(log.filter((entry) => entry.method === "POST").map((entry) => entry.path)).toEqual([`/api/v1/agent/pending/${ID}/reject`]);

    const other = await page.context().newPage();
    await installMockApi(other, mockState());
    await installPending(other, null);
    await other.goto(`/?confirm=${ID}`);
    const denied = other.getByRole("dialog", { name: "Подтверждение действия агента" });
    await expect(denied.getByText(/предложено другому пользователю/)).toBeVisible();
    await expect(denied.getByRole("button", { name: "Подтвердить" })).toHaveCount(0);
});
