import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { join } from "node:path";

const timestamp = "2026-11-10T10:00:00Z";
const user = { id: 1, username: "vera", first_name: "Вера", last_name: "Иванова", middle_name: null, is_active: true, has_avatar: false };
const project = { id: 1, key: "DEMO", name: "Интеграция CRM", description_md: "Запуск CRM", status: "ACTIVE", color: "#7299cc", icon: null, start_date: null, due_date: null, order_index: 0, created_at: timestamp, updated_at: timestamp };
const checklist = { title: "Приёмка", items: [{ id: "00000000-0000-4000-8000-000000000001", text: "Сверить контракт", is_completed: false }] };
const checklistSuggestion = { checklist: { title: "Предложение", items: [{ id: "00000000-0000-4000-8000-000000000002", text: "Проверить ответ API", is_completed: false }] }, warnings: [] };
const task = { revision: 1, id: 7, project_id: 1, stage_id: 1, key: "DEMO-7", number: 7, title: "Согласовать контракт API", description_md: "Проверить контракт поставщика", priority: "HIGH", position: 1000, start_date: null, due_date: null, baseline_start_date: null, baseline_due_date: null, completed_at: null, assignee: null, participants: [], wbs_node_id: null, created_at: timestamp, updated_at: timestamp, comments_count: 0, last_comment: null, checklist, checklist_revision: 1 };
const report = {
    id: 1, scope: "PROJECT", project_id: 1, project_key: "DEMO", project_name: project.name,
    created_at: timestamp, created_by: "Вера", llm_model: "test", duration_ms: 1,
    headline: "Сохранённый разбор проекта", health: "STABLE", health_note: "Работы идут по плану.", findings: [], progress: [], recommendations: [],
    signals: { total_tasks: 1, done_tasks: 0, overdue_tasks: 0, due_soon_tasks: 0, no_due_date_tasks: 0, unassigned_tasks: 0, stale_tasks: 0, blocked_tasks: 0, milestones_at_risk: 0, unplaced_tasks: 0 },
    context: { projects: 1, tasks_total: 1, tasks_included: 1, comments_included: 0, documents_included: 0, stickers_included: 0, milestones_included: 0, activity_included: 0, truncated: false, omitted: [], wbs_nodes_included: 0 },
};
const riskSuggestion = { title: "Задержка контракта", description: "Поставщик задерживает API", probability: "HIGH", impact: "MEDIUM", response_strategy: "MITIGATE", mitigation_plan: "Согласовать контракт", response_plan: "Запустить резервный API", task_id: null, evidence: ["Срок поставщика"] };
const riskSummary = { total_risks: 0, active_risks: 0, open_risks: 0, mitigating_risks: 0, occurred_risks: 0, closed_risks: 0, high_risks: 0, medium_risks: 0, low_risks: 0, risks_without_owner: 0, risks_without_mitigation: 0, risks_due_for_review: 0, risks_review_overdue: 0, risks_linked_to_tasks: 0, ai_suggested_risks: 0, latest_update: null, matrix: [], signals: [] };
const aiPaths = {
    checklist: "/api/v1/projects/1/tasks/checklist-suggestion",
    rephrase: "/api/v1/projects/1/tasks/rephrase",
    structure: "/api/v1/projects/1/wbs/suggestion",
    risks: "/api/v1/projects/1/risks/suggestions",
    riskField: "/api/v1/projects/1/risks/field-suggestion",
    summary: "/api/v1/dashboard/analytics",
};

interface ResponseGate {
    promise: Promise<{ json: unknown; status: number }>;
    released: boolean;
    release: (json: unknown, status?: number) => void;
}
function responseGate(): ResponseGate {
    let resolve!: (result: { json: unknown; status: number }) => void;
    const gate: ResponseGate = {
        promise: new Promise(yes => { resolve = yes; }),
        released: false,
        release(json, status = 200) { gate.released = true; resolve({ json, status }); },
    };
    return gate;
}
async function mockProject(page: Page, info: TestInfo) {
    const state = { aiPosts: [] as string[], writes: [] as string[], errors: [] as string[], gates: new Map<string, ResponseGate[]>() };
    page.on("pageerror", error => state.errors.push(error.message));
    await page.route("https://fonts.googleapis.com/**", route => route.fulfill({ status: 200, contentType: "text/css", body: "" }));
    await page.route("**/api/v1/**", async route => {
        const request = route.request();
        const path = new URL(request.url()).pathname;
        const method = request.method();
        const headers = { "access-control-allow-origin": new URL(info.project.use.baseURL!).origin, "access-control-allow-credentials": "true", "access-control-allow-headers": "content-type", "access-control-allow-methods": "GET, POST, PATCH, DELETE, OPTIONS" };
        const reply = (json: unknown, status = 200) => route.fulfill({ status, json, headers });
        if (method === "OPTIONS") return route.fulfill({ status: 204, headers });
        if (method === "POST" && Object.values(aiPaths).includes(path)) {
            state.aiPosts.push(path);
            const gate = state.gates.get(path)?.shift();
            if (!gate) throw new Error(`AI-запрос без управляемого ответа: ${path}`);
            const result = await gate.promise;
            return reply(result.json, result.status);
        }
        if (method === "POST" && path === "/api/v1/tasks/7/view") return reply({ revision: 1, deadline_revision: 1, overdue: false });
        if (method !== "GET") state.writes.push(`${method} ${path}`);
        if (path.endsWith("/auth/me")) return reply(user);
        if (path === "/api/v1/projects") return reply([project]);
        if (path === "/api/v1/projects/1") return reply(project);
        if (path === "/api/v1/users/me/task-attention") return reply({ new_tasks: 0, overdue_tasks: 0, updated_tasks: 0, total: 0, items: [] });
        if (path.endsWith("/members")) return reply([{ id: 1, project_id: 1, role: "OWNER", user, created_at: timestamp }]);
        if (path.endsWith("/stages")) return reply([{ id: 1, project_id: 1, name: "В работе", color: "#7299cc", position: 1000, is_default: true, is_done_stage: false }]);
        if (path.endsWith("/stats")) return reply({ project_id: 1, total_tasks: 1, done_tasks: 0, in_progress_tasks: 1, overdue_tasks: 0, due_soon_tasks: 0, unassigned_tasks: 0, completion_rate: 0, next_due_date: null, stage_breakdown: [] });
        if (path === "/api/v1/tasks/7") return reply(task);
        if (path === "/api/v1/tasks/7/attention") return reply({ revision: 1, deadline_revision: 1, overdue: false });
        if (path.endsWith("/tasks")) return reply([task]);
        if (path === aiPaths.summary) return reply(report);
        if (path.endsWith("/risks/summary")) return reply(riskSummary);
        if (path.endsWith("/risks/task-counts")) return reply({});
        if (path.endsWith("/risks")) return reply({ items: [], total: 0, page: 1, page_size: 25 });
        if (path.endsWith("/wbs")) return reply({ nodes: [], tasks: [task], stats: { total_nodes: 0, total_tasks: 1, assigned_tasks: 0, unassigned_tasks: 1, done_tasks: 0, overdue_tasks: 0 } });
        if (path.endsWith("/chat")) return reply({ id: 1, project_id: 1, event_cursor: 0, last_read_seq: 0, unread_count: 0, members: [] });
        if (path.endsWith("/chat/events")) return reply({ events: [], cursor: 0, has_more: false });
        return reply([]);
    });
    return {
        ...state,
        hold(path: string) {
            const gate = responseGate();
            state.gates.set(path, [...(state.gates.get(path) ?? []), gate]);
            return gate;
        },
    };
}
async function expectWaiting(page: Page, message: string, reducedMotion: boolean) {
    const status = page.getByRole("status").filter({ hasText: message });
    await expect(status).toHaveCount(1);
    await status.scrollIntoViewIfNeeded();
    await expect(status).toBeInViewport();
    await expect(status).toHaveAttribute("aria-live", "polite");
    await expect(status).toHaveAttribute("aria-atomic", "true");
    expect(await status.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    if (reducedMotion) expect(await status.locator("svg").evaluate(element => getComputedStyle(element).animationName)).toBe("none");
    return status;
}

for (const viewport of [{ name: "desktop", width: 1440, height: 1000 }, { name: "mobile", width: 390, height: 844 }]) {
    test.describe(viewport.name, () => {
        const reducedMotion = viewport.name === "mobile";
        test.use({ viewport: { width: viewport.width, height: viewport.height }, reducedMotion: reducedMotion ? "reduce" : "no-preference" });
        let state: Awaited<ReturnType<typeof mockProject>>;
        test.beforeEach(async ({ page }, info) => { state = await mockProject(page, info); });
        test.afterEach(() => {
            expect(state.errors, "Ошибки приложения").toEqual([]);
            expect(state.writes, "Генерация не выполняет другие записи и не запускает runtime Ариса").toEqual([]);
        });

        test("чек-лист: ожидание, успех и ошибка сохраняют текущие пункты", async ({ page }, info) => {
            await page.goto("/projects/DEMO/tasks");
            await page.getByRole("button", { name: "DEMO-7", exact: true }).click();
            const dialog = page.getByRole("dialog", { name: "Задача DEMO-7", exact: true });
            await dialog.getByRole("tab", { name: "Чек-лист", exact: true }).click();
            const gate = state.hold(aiPaths.checklist);
            await dialog.getByRole("button", { name: "Сформировать чек-лист", exact: true }).click();
            const message = "Арис готовит чек-лист задачи…";
            const status = await expectWaiting(page, message, reducedMotion);
            await expect(dialog.getByRole("button", { name: "Формирование…", exact: true })).toBeDisabled();
            await expect(dialog.getByRole("checkbox", { name: "Сверить контракт", exact: true })).toBeDisabled();
            expect(state.aiPosts).toEqual([aiPaths.checklist]);
            expect(gate.released).toBe(false);
            await page.screenshot({ path: join(process.env.TELOS_AI_ARTIFACTS ?? info.outputDir, `ai-operation-${viewport.name}.png`), fullPage: true });
            expect(gate.released).toBe(false);
            gate.release(checklistSuggestion);
            await expect(status).toHaveCount(0);
            await expect(dialog.getByRole("region", { name: "Предложение AI" })).toBeVisible();
            await expect(dialog.getByRole("checkbox", { name: "Сверить контракт", exact: true })).toBeEnabled();
            await dialog.getByRole("button", { name: "Отклонить предложение", exact: true }).click();
            const failure = state.hold(aiPaths.checklist);
            await dialog.getByRole("button", { name: "Сформировать чек-лист", exact: true }).click();
            await expectWaiting(page, message, reducedMotion);
            failure.release({ detail: "Провайдер недоступен" }, 503);
            await expect(status).toHaveCount(0);
            await expect(dialog.getByRole("alert")).toContainText("Провайдер недоступен");
            await expect(dialog.getByRole("checkbox", { name: "Сверить контракт", exact: true })).toBeEnabled();
            expect(state.aiPosts).toEqual([aiPaths.checklist, aiPaths.checklist]);
        });

        test("переформулирование: текст сохранён до ответа и после ошибки", async ({ page }) => {
            await page.goto("/projects/DEMO/tasks");
            await page.getByRole("button", { name: "Задача", exact: true }).click();
            const dialog = page.getByRole("dialog", { name: "Новая задача", exact: true });
            await dialog.getByLabel("Название", { exact: true }).fill("Проверить API");
            const description = dialog.locator("#new-task-description");
            await description.fill("Исходный черновик");
            const gate = state.hold(aiPaths.rephrase);
            await dialog.getByRole("button", { name: "Переформулировать", exact: true }).click();
            const message = "Арис переформулирует описание задачи…";
            const status = await expectWaiting(page, message, reducedMotion);
            await expect(description).toHaveValue("Исходный черновик");
            await expect(description).toBeDisabled();
            await expect(dialog.getByRole("button", { name: "Переформулируем…", exact: true })).toBeDisabled();
            gate.release({ description_md: "Уточнённая формулировка" });
            await expect(status).toHaveCount(0);
            await expect(description).toHaveValue("Уточнённая формулировка");
            const failure = state.hold(aiPaths.rephrase);
            await dialog.getByRole("button", { name: "Переформулировать", exact: true }).click();
            await expectWaiting(page, message, reducedMotion);
            failure.release({ detail: "Провайдер недоступен" }, 503);
            await expect(status).toHaveCount(0);
            await expect(description).toBeEnabled();
            await expect(description).toHaveValue("Уточнённая формулировка");
            await expect(dialog.getByRole("alert")).toContainText("Исходный текст не изменён");
            expect(state.aiPosts).toEqual([aiPaths.rephrase, aiPaths.rephrase]);
        });

        test("ИСР: ожидание снимается при ошибке и готовом предложении", async ({ page }) => {
            await page.goto("/projects/DEMO/structure");
            const message = "Арис готовит предложение ИСР…";
            const failure = state.hold(aiPaths.structure);
            await page.getByRole("button", { name: "Предложить ИСР", exact: true }).click();
            const status = await expectWaiting(page, message, reducedMotion);
            await expect(page.getByRole("button", { name: "Думает…", exact: true })).toBeDisabled();
            await expect(page.getByText(/0 разделов ·/)).toContainText("1 в пуле");
            failure.release({ detail: "Провайдер недоступен" }, 503);
            await expect(status).toHaveCount(0);
            await expect(page.getByText(/Не удалось предложить структуру: Провайдер недоступен/)).toBeVisible();
            const gate = state.hold(aiPaths.structure);
            await page.getByRole("button", { name: "Предложить ИСР", exact: true }).click();
            await expectWaiting(page, message, reducedMotion);
            gate.release({ nodes: [], assignments: [], skipped_task_ids: [7], summary: "Предложение подготовлено для проверки" });
            await expect(status).toHaveCount(0);
            await expect(page.getByRole("complementary", { name: "Предложенная структура ИСР" })).toContainText("Предложение подготовлено для проверки");
            expect(state.aiPosts).toEqual([aiPaths.structure, aiPaths.structure]);
        });

        test("риски: предыдущие предложения остаются после неудачного обновления", async ({ page }) => {
            await page.goto("/projects/DEMO/risks");
            const gate = state.hold(aiPaths.risks);
            await page.getByRole("button", { name: "Предложить риски", exact: true }).click();
            const message = "Арис изучает проект и предлагает риски…";
            const status = await expectWaiting(page, message, reducedMotion);
            await expect(page.getByRole("button", { name: "Изучаю проект…", exact: true })).toBeDisabled();
            gate.release({ suggestions: [riskSuggestion] });
            await expect(status).toHaveCount(0);
            await expect(page.getByText(riskSuggestion.title, { exact: true })).toBeVisible();
            const failure = state.hold(aiPaths.risks);
            await page.getByRole("button", { name: "Предложить риски", exact: true }).click();
            await expectWaiting(page, message, reducedMotion);
            await expect(page.getByText(riskSuggestion.title, { exact: true })).toBeVisible();
            failure.release({ detail: "Провайдер недоступен" }, 503);
            await expect(status).toHaveCount(0);
            await expect(page.getByText(riskSuggestion.title, { exact: true })).toBeVisible();
            await expect(page.getByRole("alert")).toContainText("Провайдер недоступен");
            expect(state.aiPosts).toEqual([aiPaths.risks, aiPaths.risks]);
        });

        test("поле риска: черновик и готовое предложение сохраняются", async ({ page }) => {
            await page.goto("/projects/DEMO/risks");
            await page.getByRole("button", { name: "Добавить риск", exact: true }).click();
            const dialog = page.getByRole("dialog", { name: "Новый риск", exact: true });
            const description = dialog.getByLabel("Описание *", { exact: true });
            await description.fill("Мой черновик риска");
            const gate = state.hold(aiPaths.riskField);
            await dialog.getByRole("button", { name: "Помочь сформулировать", exact: true }).first().click();
            const message = "Арис формулирует описание риска…";
            const status = await expectWaiting(page, message, reducedMotion);
            await expect(dialog.getByRole("button", { name: "Формулируем…", exact: true })).toBeDisabled();
            await expect(description).toHaveValue("Мой черновик риска");
            gate.release({ field: "description", text: "Предложение описания риска", warnings: [] });
            await expect(status).toHaveCount(0);
            await expect(dialog.getByText("Предложение описания риска", { exact: true })).toBeVisible();
            await expect(description).toHaveValue("Мой черновик риска");
            const failure = state.hold(aiPaths.riskField);
            await dialog.getByRole("button", { name: "Помочь сформулировать", exact: true }).first().click();
            await expectWaiting(page, message, reducedMotion);
            failure.release({ detail: "Провайдер недоступен" }, 503);
            await expect(status).toHaveCount(0);
            await expect(description).toHaveValue("Мой черновик риска");
            await expect(dialog.getByText("Предложение описания риска", { exact: true })).toBeVisible();
            await expect(dialog.getByRole("alert")).toContainText("Провайдер недоступен");
            expect(state.aiPosts).toEqual([aiPaths.riskField, aiPaths.riskField]);
        });

        test("разбор: предыдущий результат доступен во время ожидания и после ошибки", async ({ page }) => {
            await page.goto("/projects/DEMO");
            const summary = page.getByRole("region", { name: "Вывод Ариса", exact: true });
            await expect(summary.getByText(report.headline, { exact: true })).toBeVisible();
            const gate = state.hold(aiPaths.summary);
            await summary.getByRole("button", { name: "Обновить разбор", exact: true }).click();
            const message = "Арис готовит разбор проекта…";
            const status = await expectWaiting(page, message, reducedMotion);
            await expect(summary.getByRole("button", { name: "Разбираю…", exact: true })).toBeDisabled();
            await expect(summary.getByText(report.headline, { exact: true })).toBeVisible();
            gate.release({ ...report, id: 2, headline: "Обновлённый разбор проекта" });
            await expect(status).toHaveCount(0);
            await expect(summary.getByText("Обновлённый разбор проекта", { exact: true })).toBeVisible();
            const failure = state.hold(aiPaths.summary);
            await summary.getByRole("button", { name: "Обновить разбор", exact: true }).click();
            await expectWaiting(page, message, reducedMotion);
            failure.release({ detail: "Провайдер недоступен" }, 503);
            await expect(status).toHaveCount(0);
            await expect(summary.getByText("Обновлённый разбор проекта", { exact: true })).toBeVisible();
            await expect(summary.getByRole("alert")).toContainText("Провайдер недоступен");
            expect(state.aiPosts).toEqual([aiPaths.summary, aiPaths.summary]);
        });
    });
}
