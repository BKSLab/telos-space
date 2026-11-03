import { expect, test, type Page } from "@playwright/test";
import type { TaskChecklist } from "../src/lib/checklists";

const timestamp = "2026-09-06T10:00:00Z";
const user = { id: 1, username: "vera", first_name: "Вера", last_name: "Иванова", middle_name: null, is_active: true, has_avatar: false };
const project = { id: 1, key: "DEMO", name: "Интеграция CRM", description_md: "Запуск CRM", status: "ACTIVE", color: "#7299cc", icon: null, start_date: null, due_date: null, order_index: 0, created_at: timestamp, updated_at: timestamp };
const baseTask = { id: 7, project_id: 1, stage_id: 1, key: "DEMO-142", number: 142, title: "Согласовать контракт API", description_md: "Проверить контракт поставщика", priority: "HIGH", position: 1000, start_date: null, due_date: null, executor: null, participants: [], wbs_node_id: null, created_at: timestamp, updated_at: timestamp };
const checklist = (): TaskChecklist => ({ title: "Приёмка", items: [
    { id: "00000000-0000-4000-8000-000000000001", text: "Сверить поля запроса", is_completed: false },
    { id: "00000000-0000-4000-8000-000000000002", text: "Согласовать ответ", is_completed: false },
] });
const suggestion = (): TaskChecklist => ({ title: "Чек-лист", items: [
    { id: "00000000-0000-4000-8000-000000000003", text: "Проверить обязательные поля", is_completed: false },
    { id: "00000000-0000-4000-8000-000000000004", text: "Согласовать ошибки API", is_completed: false },
    { id: "00000000-0000-4000-8000-000000000005", text: "Зафиксировать контракт", is_completed: false },
] });

async function mockProject(page: Page, initial: TaskChecklist | null = checklist()) {
    const state = {
        task: { ...baseTask, checklist: initial, checklist_revision: initial ? 1 : 0 },
        writes: [] as { method: string; body: Record<string, unknown> }[],
        generations: [] as string[], errors: [] as string[], aiError: false,
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
        if (path === "/api/v1/projects") return reply([project]);
        if (path.endsWith("/stats")) return reply({ project_id: 1, total_tasks: 1, done_tasks: 0, in_progress_tasks: 1, overdue_tasks: 0, due_soon_tasks: 0, unassigned_tasks: 0, completion_rate: 0, next_due_date: null, stage_breakdown: [] });
        if (path.endsWith("/members")) return reply([{ id: 1, project_id: 1, role: "OWNER", user, created_at: timestamp }]);
        if (path.endsWith("/stages")) return reply([{ id: 1, project_id: 1, name: "В работе", color: "#7299cc", position: 1000, is_default: true, is_done_stage: false }]);
        if (path.endsWith("/checklist-suggestion")) {
            state.generations.push(request.postData() ?? "");
            return state.aiError ? reply({ detail: "AI-провайдер недоступен" }, 503) : reply({ checklist: suggestion(), warnings: ["Файл «scan.pdf» передан ограниченным фрагментом."] });
        }
        if (path === "/api/v1/tasks/7" || path === "/api/v1/projects/1/tasks") {
            if (method === "POST" || method === "PATCH") {
                const body = request.postDataJSON();
                state.writes.push({ method, body });
                if (method === "PATCH" && body.checklist_revision !== state.task.checklist_revision) return reply({ detail: "Чек-лист уже изменён. Загрузите актуальную версию." }, 409);
                state.task = { ...state.task, ...body, stage_id: body.stage_id ?? 1, checklist_revision: method === "POST" ? (body.checklist ? 1 : 0) : state.task.checklist_revision + 1 };
                return reply(state.task, method === "POST" ? 201 : 200);
            }
            return reply(path.endsWith("/tasks") ? [state.task] : state.task);
        }
        if (path.endsWith("/risks/task-counts")) return reply({});
        if (path.endsWith("/risks")) return reply({ items: [], total: 0, page: 1, page_size: 25 });
        if (path.endsWith("/wbs")) return reply({ nodes: [], unassigned_tasks: [] });
        return reply([]);
    });
    return state;
}

async function openTask(page: Page) {
    await page.goto("/projects/DEMO/tasks");
    await page.getByRole("button", { name: "DEMO-142", exact: true }).click();
    const card = page.getByRole("dialog", { name: "Задача DEMO-142", exact: true });
    await card.getByRole("tab", { name: "Чек-лист", exact: true }).click();
    return card;
}

test("ручной чек-лист создаётся атомарно с задачей; пустой пункт нельзя сохранить", async ({ page }) => {
    const state = await mockProject(page, null);
    await page.goto("/projects/DEMO/tasks");
    await page.getByRole("button", { name: "Задача", exact: true }).click();
    const modal = page.getByRole("dialog", { name: "Новая задача", exact: true });
    await modal.getByLabel("Название", { exact: true }).fill("Проверить API");
    await modal.getByRole("button", { name: "Добавить чек-лист", exact: true }).click();
    await expect(modal.getByRole("button", { name: "Создать задачу", exact: true })).toBeDisabled();
    await modal.getByLabel("Название чек-листа", { exact: true }).fill("Приёмка");
    await modal.getByLabel("Пункт 1", { exact: true }).fill("Проверить запрос");
    await modal.getByRole("button", { name: "Добавить пункт", exact: true }).click();
    await modal.getByLabel("Пункт 2", { exact: true }).fill("Согласовать ответ");
    await modal.getByRole("button", { name: "Поднять пункт 2", exact: true }).click();
    expect(state.writes).toEqual([]);
    await modal.getByRole("button", { name: "Создать задачу", exact: true }).click();
    await expect(modal).not.toBeVisible();
    expect(state.writes).toHaveLength(1);
    expect(state.writes[0]).toMatchObject({ method: "POST", body: { title: "Проверить API", checklist: { title: "Приёмка", items: [{ text: "Согласовать ответ", is_completed: false }, { text: "Проверить запрос", is_completed: false }] } } });
    expect(state.task.checklist!.items[0].id).toMatch(/^[a-f0-9-]{36}$/);
    expect(state.errors).toEqual([]);
});

test("AI при создании использует поля и файл; предложение принимается отдельно", async ({ page }) => {
    const state = await mockProject(page, null);
    await page.goto("/projects/DEMO/tasks");
    await page.getByRole("button", { name: "Задача", exact: true }).click();
    const modal = page.getByRole("dialog", { name: "Новая задача", exact: true });
    await modal.getByLabel("Название", { exact: true }).fill("Проверить API");
    await modal.locator("#new-task-description").fill("Описание с критериями");
    await modal.locator('input[type="file"]').setInputFiles({ name: "contract.txt", mimeType: "text/plain", buffer: Buffer.from("Версия контракта 2") });
    await modal.getByRole("button", { name: "Сформировать чек-лист", exact: true }).click();
    const proposal = modal.getByRole("region", { name: "Предложение AI" });
    await expect(proposal.getByText("Файл «scan.pdf» передан ограниченным фрагментом.")).toBeVisible();
    expect(state.generations[0]).toContain("Описание с критериями");
    expect(state.generations[0]).toContain('filename="contract.txt"');
    expect(state.writes).toEqual([]);
    await proposal.getByRole("button", { name: "Отклонить предложение", exact: true }).click();
    await expect(proposal).not.toBeVisible();
    await modal.getByRole("button", { name: "Сформировать чек-лист", exact: true }).click();
    await proposal.getByLabel("Пункт 1", { exact: true }).fill("Проверить поля версии 2");
    await proposal.getByRole("button", { name: "Принять чек-лист", exact: true }).click();
    await expect(proposal).not.toBeVisible();
    expect(state.writes).toEqual([]);
    await modal.getByRole("button", { name: "Создать задачу", exact: true }).click();
    await expect(modal).not.toBeVisible();
    expect(state.writes).toHaveLength(1);
    expect(state.task.checklist?.items[0].text).toBe("Проверить поля версии 2");
    expect(state.task.checklist?.items).toHaveLength(3);
    expect(state.errors).toEqual([]);
});

test("карточка: выполнение, порядок, изменение пунктов, удаление и повторное создание", async ({ page }) => {
    const state = await mockProject(page);
    const drawer = await openTask(page);
    const section = drawer.getByRole("region", { name: "Чек-лист задачи" });
    await section.getByRole("checkbox", { name: "Сверить поля запроса", exact: true }).check();
    await expect(section.getByRole("checkbox", { name: "Сверить поля запроса", exact: true })).toBeEnabled();
    expect(state.task.checklist?.items[0].is_completed).toBe(true);
    expect(state.task.stage_id).toBe(1);
    await section.getByRole("button", { name: "Редактировать чек-лист", exact: true }).click();
    await section.getByLabel("Название чек-листа", { exact: true }).fill("Проверки API");
    await section.getByRole("button", { name: "Поднять пункт 2", exact: true }).click();
    await section.getByLabel("Пункт 1", { exact: true }).fill("Проверить ответ сервера");
    await section.getByRole("button", { name: "Удалить пункт 2", exact: true }).click();
    await section.getByRole("button", { name: "Добавить пункт", exact: true }).click();
    await section.getByLabel("Пункт 2", { exact: true }).fill("Зафиксировать результат");
    await drawer.getByRole("button", { name: "Сохранить чек-лист", exact: true }).click();
    await expect(section.getByRole("checkbox", { name: "Проверить ответ сервера", exact: true })).toBeVisible();
    expect(state.writes[1].body.checklist_revision).toBe(2);
    expect(state.task.checklist?.items.map(item => item.text)).toEqual(["Проверить ответ сервера", "Зафиксировать результат"]);
    await section.getByRole("button", { name: "Удалить чек-лист", exact: true }).click();
    const confirmation = page.getByRole("dialog", { name: "Удалить чек-лист?", exact: true });
    await confirmation.getByRole("button", { name: "Отмена", exact: true }).click();
    expect(state.writes).toHaveLength(2);
    await section.getByRole("button", { name: "Удалить чек-лист", exact: true }).click();
    await confirmation.getByRole("button", { name: "Удалить чек-лист", exact: true }).click();
    await expect(section.getByRole("button", { name: "Добавить чек-лист", exact: true })).toBeVisible();
    expect(state.task.checklist).toBeNull();
    await section.getByRole("button", { name: "Добавить чек-лист", exact: true }).click();
    await section.getByLabel("Пункт 1", { exact: true }).fill("Новая проверка");
    await drawer.getByRole("button", { name: "Сохранить чек-лист", exact: true }).click();
    await expect(section.getByRole("checkbox", { name: "Новая проверка", exact: true })).toBeVisible();
    expect(state.task.checklist_revision).toBe(5);
    expect(state.errors).toEqual([]);
});

test("конфликт сохраняет локальный черновик и позволяет загрузить текущую версию", async ({ page }) => {
    const state = await mockProject(page);
    const drawer = await openTask(page);
    const section = drawer.getByRole("region", { name: "Чек-лист задачи" });
    await section.getByRole("button", { name: "Редактировать чек-лист", exact: true }).click();
    await section.getByLabel("Пункт 1", { exact: true }).fill("Локальная правка");
    await drawer.getByRole("tab", { name: "Основное", exact: true }).click();
    await drawer.getByRole("tab", { name: "Чек-лист", exact: true }).click();
    await expect(section.getByLabel("Пункт 1", { exact: true })).toHaveValue("Локальная правка");
    state.task.checklist_revision = 2;
    state.task.checklist!.items[0].text = "Правка коллеги";
    await drawer.getByRole("button", { name: "Сохранить чек-лист", exact: true }).click();
    await expect(drawer.getByText("Чек-лист уже изменён. Загрузите актуальную версию.")).toBeVisible();
    await expect(section.getByLabel("Пункт 1", { exact: true })).toHaveValue("Локальная правка");
    expect(state.task.checklist?.items[0].text).toBe("Правка коллеги");
    await drawer.getByRole("button", { name: "Загрузить актуальный чек-лист", exact: true }).click();
    await expect(section.getByRole("checkbox", { name: "Правка коллеги", exact: true })).toBeVisible();
    expect(state.errors).toEqual([]);
});

test("AI в карточке: ошибка не меняет чек-лист, замена требует принятия", async ({ page }) => {
    const state = await mockProject(page);
    state.aiError = true;
    const drawer = await openTask(page);
    const section = drawer.getByRole("region", { name: "Чек-лист задачи" });
    await section.getByRole("button", { name: "Сформировать чек-лист", exact: true }).click();
    await expect(section.getByText("AI-провайдер недоступен")).toBeVisible();
    await expect(section.getByRole("checkbox", { name: "Сверить поля запроса", exact: true })).toBeVisible();
    expect(state.writes).toEqual([]);
    state.aiError = false;
    await section.getByRole("button", { name: "Сформировать чек-лист", exact: true }).click();
    const proposal = section.getByRole("region", { name: "Предложение AI" });
    await expect(proposal).toBeVisible();
    expect(state.generations[1]).toContain('"task_id":7');
    expect(state.generations[1]).toContain("Сверить поля запроса");
    expect(state.writes).toEqual([]);
    await proposal.getByLabel("Пункт 1", { exact: true }).fill("Проверить контракт версии 2");
    await proposal.getByRole("button", { name: "Заменить чек-лист", exact: true }).click();
    await expect(proposal).not.toBeVisible();
    await expect(section.getByRole("checkbox", { name: "Проверить контракт версии 2", exact: true })).toBeVisible();
    expect(state.writes).toHaveLength(1);
    expect(state.writes[0].body.checklist_revision).toBe(1);
    expect(state.errors).toEqual([]);
});

test("на узком экране пункты и AI-редактор помещаются в карточке", async ({ page }, info) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const state = await mockProject(page);
    const drawer = await openTask(page);
    const section = drawer.getByRole("region", { name: "Чек-лист задачи" });
    await section.getByRole("button", { name: "Сформировать чек-лист", exact: true }).click();
    const proposal = section.getByRole("region", { name: "Предложение AI" });
    await expect(proposal).toBeVisible();
    await proposal.getByLabel("Пункт 1", { exact: true }).fill("Длинный пункт ".repeat(30));
    await proposal.scrollIntoViewIfNeeded();
    expect(await drawer.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath("task-checklist-mobile.png"), fullPage: true });
    expect(state.errors).toEqual([]);
});
