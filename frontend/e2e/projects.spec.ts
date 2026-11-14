import { expect, test, type Page } from "@playwright/test";
import type { Project, ProjectDeadlineChange, ProjectStage, ProjectUpdate } from "../src/lib/types";

const timestamp = "2026-09-07T10:00:00Z";
const user = { id: 1, username: "vera", first_name: "Вера", last_name: "Иванова", middle_name: null, is_active: true, has_avatar: false };
const baseProject: Project = {
    id: 1, key: "DEMO", name: "Интеграция CRM", owner_id: 1, description_md: "Старое **описание**",
    status: "ACTIVE", color: "#7299cc", icon: null, start_date: "2026-09-01", due_date: "2026-10-01",
    due_date_has_been_set: true, order_index: 0, created_at: timestamp, updated_at: timestamp,
};
const defaults = [
    { name: "Бэклог", color: "#7299cc", is_done_stage: false },
    { name: "В работе", color: "#7299cc", is_done_stage: false },
    { name: "Готово", color: "#7299cc", is_done_stage: true },
];

async function mockProjects(page: Page, initial = baseProject) {
    const state = {
        project: { ...initial }, writes: [] as Record<string, unknown>[], history: [] as ProjectDeadlineChange[],
        stages: defaults.map((stage, index) => ({ ...stage, id: index + 1, project_id: 1, order_index: index })) as ProjectStage[],
        errors: [] as string[], conflict: false,
    };
    page.on("pageerror", error => state.errors.push(error.message));
    await page.route("**/api/v1/**", async route => {
        const request = route.request();
        const path = new URL(request.url()).pathname;
        const method = request.method();
        const headers = { "access-control-allow-origin": "http://127.0.0.1:4179", "access-control-allow-credentials": "true", "access-control-allow-headers": "content-type", "access-control-allow-methods": "GET, POST, PATCH, DELETE, OPTIONS" };
        const reply = (json: unknown, status = 200) => route.fulfill({ status, json, headers });
        if (method === "OPTIONS") return route.fulfill({ status: 204, headers });
        if (path.endsWith("/auth/me")) return reply(user);
        if (path.endsWith("/projects/defaults")) return reply({ stages: defaults });
        if (path.endsWith("/deadline-history")) return reply(state.history);
        if (path.endsWith("/members")) return reply([{ id: 1, project_id: 1, role: "OWNER", user, created_at: timestamp }]);
        if (path.endsWith("/stages")) return reply(state.stages);
        if (path.endsWith("/stats")) return reply({ project_id: 1, total_tasks: 0, done_tasks: 0, in_progress_tasks: 0, overdue_tasks: 0, due_soon_tasks: 0, unassigned_tasks: 0, completion_rate: 0, next_due_date: null, stage_breakdown: [] });
        if (path === "/api/v1/projects" && method === "POST") {
            const body = request.postDataJSON();
            state.writes.push(body);
            state.project = { ...baseProject, ...body };
            return reply(state.project, 201);
        }
        if (path === "/api/v1/projects/1" && method === "PATCH") {
            const body: ProjectUpdate = request.postDataJSON();
            state.writes.push({ ...body });
            if (state.conflict) {
                state.conflict = false;
                state.project.due_date = "2026-11-01";
                return reply({ detail: "Срок проекта уже изменён. Обновите данные и повторите изменение." }, 409);
            }
            if ("due_date" in body && body.due_date !== state.project.due_date) {
                state.history.unshift({ id: state.history.length + 1, previous_due_date: state.project.due_date,
                    new_due_date: body.due_date ?? null, comment: body.due_date_comment ?? null,
                    changed_by_user_id: 1, changed_by_name: "Иванова Вера", created_at: timestamp });
                state.project.due_date_has_been_set = true;
            }
            state.project = { ...state.project, ...body };
            return reply(state.project);
        }
        if (path === "/api/v1/projects") return reply([state.project]);
        if (path === "/api/v1/projects/1") return reply(state.project);
        if (path === "/api/v1/documents") return reply({ items: [], total: 0, limit: 20, offset: 0, next_offset: null });
        return reply([]);
    });
    return state;
}

test("создание: паспорт, руководитель, команда и свои стадии в одном запросе", async ({ page }, testInfo) => {
    const state = await mockProjects(page);
    await page.goto("/projects/new");
    await expect(page.getByText("Иванова Вера", { exact: false }).first()).toBeVisible();
    await page.getByLabel("Код", { exact: true }).fill("NEW");
    await page.getByLabel("Название", { exact: true }).fill("Приёмка поставок");
    await page.getByLabel("Проблема", { exact: true }).fill("Теряются заявки");
    await page.getByLabel("Цель", { exact: true }).fill("Все заявки учтены");
    await page.getByLabel("Ожидаемый результат", { exact: true }).fill("Работающая очередь заявок");
    await page.getByLabel("Команда", { exact: true }).fill("anna, ivan, Anna");
    await page.getByLabel("Название стадии 1", { exact: true }).fill("Заявки");
    await page.getByRole("button", { name: "Поднять стадию 3", exact: true }).click();
    await page.screenshot({ path: testInfo.outputPath("new-project.png"), fullPage: true });
    await page.getByRole("button", { name: "Создать проект", exact: true }).click();
    await expect(page).toHaveURL(/\/projects\/NEW$/);
    expect(state.writes).toHaveLength(1);
    expect(state.writes[0]).toMatchObject({
        key: "NEW", member_usernames: ["anna", "ivan"], due_date: null,
        description_sections: { problem: "Теряются заявки", goal: "Все заявки учтены", expected_result: "Работающая очередь заявок" },
        stages: [{ name: "Заявки" }, { name: "Готово", is_done_stage: true }, { name: "В работе" }],
    });
    expect(state.writes[0].start_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(state.errors).toEqual([]);
});

test("срок: причина обязательна для переноса, удаления и повторного назначения", async ({ page }, testInfo) => {
    const state = await mockProjects(page);
    await page.goto("/projects/DEMO/settings");
    await expect(page.getByLabel("Дополнительно", { exact: true })).toHaveValue("Старое **описание**");
    const save = page.getByRole("button", { name: "Сохранить", exact: true });
    const due = page.getByLabel("Плановое завершение", { exact: true });
    const reason = page.getByLabel("Причина изменения срока", { exact: true });
    await due.fill("2026-10-15");
    await expect(save).toBeDisabled();
    await reason.fill("Перенос поставки");
    await save.click();
    await expect(reason).toHaveCount(0);
    await expect(page.getByText("Перенос поставки", { exact: true })).toBeVisible();
    expect(state.writes[0]).toMatchObject({ expected_due_date: "2026-10-01", due_date: "2026-10-15", due_date_comment: "Перенос поставки" });
    await due.fill("");
    await expect(save).toBeDisabled();
    await reason.fill("Пересматриваем план");
    await save.click();
    await expect(reason).toHaveCount(0);
    await expect(page.getByText("Пересматриваем план", { exact: true })).toBeVisible();
    await due.fill("2026-12-01");
    await expect(save).toBeDisabled();
    await reason.fill("Утвердили новый план");
    await save.click();
    await expect(reason).toHaveCount(0);
    await expect(page.getByText("Утвердили новый план", { exact: true })).toBeVisible();
    expect(state.history).toHaveLength(3);
    await page.screenshot({ path: testInfo.outputPath("project-settings.png"), fullPage: true });
    expect(state.errors).toEqual([]);
});

test("первое назначение без причины, конфликт требует повторного ввода причины", async ({ page }) => {
    const state = await mockProjects(page, { ...baseProject, due_date: null, due_date_has_been_set: false });
    await page.goto("/projects/DEMO/settings");
    await page.getByLabel("Плановое завершение", { exact: true }).fill("2026-10-01");
    await expect(page.getByLabel("Причина изменения срока", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "Сохранить", exact: true }).click();
    await expect(page.getByText("Без даты → 01.10.2026", { exact: true })).toBeVisible();
    await page.getByLabel("Плановое завершение", { exact: true }).fill("2026-12-01");
    await page.getByLabel("Причина изменения срока", { exact: true }).fill("Старое обоснование");
    state.conflict = true;
    await page.getByRole("button", { name: "Сохранить", exact: true }).click();
    await expect(page.getByText("Предыдущий срок: 2026-11-01.", { exact: true })).toBeVisible();
    await expect(page.getByLabel("Причина изменения срока", { exact: true })).toHaveValue("");
    await expect(page.getByRole("button", { name: "Сохранить", exact: true })).toBeDisabled();
    expect(state.history).toHaveLength(1);
    expect(state.errors).toEqual([]);
});

for (const width of [1440, 390, 320]) {
    test(`оформление проекта: модалка, круглые цвета и отмена на ${width}px`, async ({ page }, testInfo) => {
        await page.setViewportSize({ width, height: 900 });
        const state = await mockProjects(page);
        await page.goto("/projects/new");
        await page.getByLabel("Название", { exact: true }).fill("Мой длинный проект — оформление и документы");
        const trigger = page.getByRole("button", { name: "Оформление проекта", exact: true });
        await trigger.click();
        const dialog = page.getByRole("dialog", { name: "Оформление проекта" });
        await expect(dialog).toBeVisible();
        await dialog.evaluate(async element => {
            await Promise.all((element.parentElement?.getAnimations() ?? []).map(animation => animation.finished));
        });
        const bounds = await dialog.boundingBox();
        expect(bounds!.x).toBeGreaterThanOrEqual(0);
        expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
        const icons = dialog.getByRole("region", { name: "Иконки набора" }).getByRole("button");
        await expect(icons).toHaveCount(19); // 18 иконок и действие сброса.
        const colors = dialog.getByRole("region", { name: "Цвет проекта" }).getByRole("button");
        await expect(colors).toHaveCount(8);
        for (const button of await colors.all()) {
            const target = await button.boundingBox();
            expect(target!.width).toBeGreaterThanOrEqual(44);
            expect(target!.height).toBe(target!.width);
        }
        for (const swatch of await dialog.getByTestId("project-color-swatch").all()) {
            const box = await swatch.boundingBox();
            expect(box!.height).toBe(box!.width);
            expect(box!.height).toBe(24);
        }
        await dialog.getByRole("button", { name: "Запуск", exact: true }).click();
        await dialog.getByRole("button", { name: "Цвет #a371f7" }).click();
        await page.screenshot({ path: testInfo.outputPath(`appearance-${width}.png`), fullPage: true });
        await dialog.getByRole("button", { name: "Отмена", exact: true }).click();
        await expect(dialog).toHaveCount(0);
        await expect(page.getByLabel("Название", { exact: true })).toHaveValue("Мой длинный проект — оформление и документы");
        await expect(trigger).toBeFocused();
        await trigger.click();
        await expect(dialog.getByRole("button", { name: "Запуск", exact: true })).toHaveAttribute("aria-pressed", "false");
        await dialog.getByRole("button", { name: "Запуск", exact: true }).click();
        await dialog.getByRole("button", { name: "Цвет #a371f7" }).click();
        await dialog.getByRole("button", { name: "Применить", exact: true }).click();
        await expect(trigger).toContainText("Запуск");
        await trigger.click();
        await expect(dialog.getByRole("button", { name: "Запуск", exact: true })).toHaveAttribute("aria-pressed", "true");
        await expect(dialog.getByRole("button", { name: "Цвет #a371f7" })).toHaveAttribute("aria-pressed", "true");
        await page.keyboard.press("Escape");
        await expect(dialog).toHaveCount(0);
        expect(state.errors).toEqual([]);
    });
}

test("картинка нового проекта: локальное превью применяется и отменяется без потери формы", async ({ page }, testInfo) => {
    const state = await mockProjects(page);
    await page.goto("/projects/new");
    await page.getByLabel("Название", { exact: true }).fill("Проект с логотипом");
    const trigger = page.getByRole("button", { name: "Оформление проекта", exact: true });
    await trigger.click();
    const dialog = page.getByRole("dialog", { name: "Оформление проекта" });
    const file = { name: "logo.png", mimeType: "image/png", buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=", "base64") };
    await dialog.getByLabel("Файл картинки проекта").setInputFiles(file);
    await expect(dialog.getByTestId("icon-picker-pending")).toHaveJSProperty("naturalWidth", 1);
    await dialog.getByRole("button", { name: "Применить", exact: true }).click();
    await expect(trigger).toContainText("Свой логотип");
    await trigger.click();
    await dialog.getByRole("button", { name: "Убрать картинку", exact: true }).click();
    await dialog.getByRole("button", { name: "Отмена", exact: true }).click();
    await expect(trigger.getByTestId("icon-picker-pending")).toBeVisible();
    await expect(page.getByLabel("Название", { exact: true })).toHaveValue("Проект с логотипом");
    await page.screenshot({ path: testInfo.outputPath("project-with-pending-logo.png"), fullPage: true });
    expect(state.errors).toEqual([]);
});
