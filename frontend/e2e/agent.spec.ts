import { expect, test, type Page } from "@playwright/test";
import type { AgentToolRun, AgentFile, KnowledgeSource, TimPageContext } from "../src/lib/types";

async function openPersonalWindow(page: Page) {
    await page.getByRole("button", { name: "Открыть чаты", exact: true }).click();
    const panel = page.getByRole("dialog", { name: "Чаты", exact: true });
    await expect(panel).toBeVisible();
    await panel.getByRole("combobox", { name: "Выбор чата", exact: true }).selectOption("personal");
    await expect(panel.getByRole("textbox", { name: "Вопрос Арису", exact: true })).toBeVisible();
    return panel;
}

async function openProjectPersonal(page: Page, key = "DEMO") {
    await page.goto("/chat");
    await page.getByRole("button", { name: "Проектные диалоги и знания", exact: true }).click();
    await page.getByRole("link", { name: `${key} · Личные диалоги`, exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/projects/${key}/chat\\?view=personal(?:&conversation=\\d+)?$`));
    await expect(page.getByRole("textbox", { name: "Вопрос Арису по проекту", exact: true })).toBeEnabled();
}

test("единое окно личного Ариса сохраняет черновик, файлы и диалог при переходе во вкладку", async ({ page }) => {
    const control = await installProjectApi(page);
    control.pending = true;
    await page.goto("/projects/DEMO/team");
    await openPersonalWindow(page);
    const input = page.getByRole("textbox", { name: "Вопрос Арису", exact: true });
    await input.fill("Подготовь критерии приёмки");
    await page.getByRole("button", { name: "Свернуть чат" }).click();
    await openPersonalWindow(page);
    await expect(input).toHaveValue("Подготовь критерии приёмки");
    await page.getByLabel("Файлы для Ариса", { exact: true }).setInputFiles({ name: "acceptance.md", mimeType: "text/markdown", buffer: Buffer.from("Критерии") });
    await expect(page.getByRole("button", { name: "Убрать acceptance.md" })).toBeVisible();
    await expect(page.getByRole("combobox", { name: "Диалог с Арисом", exact: true })).toHaveValue("1");
    await page.getByRole("button", { name: "Открыть чат во вкладке" }).click();
    await expect(page).toHaveURL(/\/chat$/);
    await expect(page.getByRole("button", { name: "Открыть чаты", exact: true })).toHaveCount(0);
    await expect(page.getByRole("dialog", { name: "Чаты", exact: true })).toHaveCount(0);
    await expect(page.getByRole("combobox", { name: "Диалог с Арисом", exact: true })).toHaveValue("1");
    await expect(input).toHaveValue("Подготовь критерии приёмки");
    await expect(page.getByRole("button", { name: "Убрать acceptance.md" })).toBeVisible();
    await input.press("Enter");
    await expect(page.getByText("Арис думает…", { exact: true })).toBeVisible();
    await page.goto("/projects/DEMO/team");
    await openPersonalWindow(page);
    await expect(page.getByText("Арис думает…", { exact: true })).toBeVisible();
    control.pending = false;
    await expect(page.getByText("Руководитель проекта — Анна Иванова.", { exact: true })).toBeVisible();
    expect(control.submissions).toHaveLength(1);
    expect(control.submissions[0].file_ids).toEqual(["f918d205-2ced-48eb-85f4-19b8e55ef361"]);
    expect(control.submissions[0].page_context).toEqual({ route: "/chat", project_key: null, task_id: null });
    await expect(page).toHaveURL(/\/team$/);
});

test("карточка задачи показывает поля без повторяющего их текста и JSON результата", async ({ page }) => {
    const control = await installProjectApi(page);
    control.answer = "";
    control.sources = [{ source_id: "task:42", entity_type: "task", entity_id: 42, title: "DEMO-42 · Проверить оплату", task_id: 42, document_slug: null, score: null, excerpt: null,
        card: { key: "DEMO-42", status: "В работе", priority: "HIGH", assignee: "Анна Иванова", due_date: "2026-10-01", summary: "Проверить оплату картой и возврат средств." } }];
    control.action = { id: "create-42", tool_name: "create_task", title: "Создать задачу", arguments: { title: "Проверить оплату" }, status: "completed", result: { source_id: "task:42", task: { id: 42, key: "DEMO-42", title: "Проверить оплату" } }, created_at: "2026-09-22T10:00:00Z" };
    await page.goto("/projects/DEMO/team");
    await openPersonalWindow(page);
    await page.getByRole("textbox", { name: "Вопрос Арису", exact: true }).fill("Создай задачу на проверку оплаты");
    await page.getByRole("textbox", { name: "Вопрос Арису", exact: true }).press("Enter");
    const card = page.getByRole("button", { name: "Задача: DEMO-42 · Проверить оплату", exact: true });
    await expect(card).toBeVisible();
    await expect(card).toContainText("В работе");
    await expect(card).toContainText("Анна Иванова");
    await expect(card).toContainText("01.10.2026");
    await expect(page.getByText("DEMO-42", { exact: true })).toHaveCount(1);
    await expect(page.locator(".markdown-body")).toHaveCount(0);
    await page.screenshot({ path: "test-results/floating-agent-card.png" });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(card).toBeVisible();
    const panel = await page.getByRole("dialog", { name: "Чаты", exact: true }).boundingBox();
    expect(panel!.x).toBeGreaterThanOrEqual(0);
    expect(panel!.x + panel!.width).toBeLessThanOrEqual(390);
    await page.screenshot({ path: "test-results/floating-agent-mobile.png" });
});

test("проектный личный диалог выбирается явно на общем экране, черновики проектов не смешиваются", async ({ page }) => {
    const control = await installProjectApi(page);
    control.projects.push({ ...control.projects[0], id: 2, key: "BETA", name: "Второй проект" });
    await openProjectPersonal(page);
    const input = page.getByRole("textbox", { name: "Вопрос Арису по проекту", exact: true });
    await input.fill("Черновик первого проекта");
    await openProjectPersonal(page, "BETA");
    await expect(input).toHaveValue("");
    await input.fill("Черновик второго проекта");
    await openProjectPersonal(page);
    await expect(input).toHaveValue("Черновик первого проекта");
    await expect(page).toHaveURL(/\/projects\/DEMO\/chat\?view=personal$/);
    await expect(page.getByRole("button", { name: "Открыть чаты", exact: true })).toHaveCount(0);
    await page.reload();
    await expect(input).toHaveValue("Черновик первого проекта");
});

async function installProjectApi(page: Page) {
    const stamp = "2026-09-21T10:00:00Z";
    const user = { id: 1, username: "anna", first_name: "Анна", last_name: "Иванова", middle_name: null, is_active: true, has_avatar: false };
    const project = { id: 1, owner_id: 1, key: "DEMO", name: "Знания проекта", description_md: "Паспорт", description_sections: null, due_date_has_been_set: false, status: "ACTIVE", color: "#7299cc", icon: null, start_date: null, due_date: null, order_index: 0, created_at: stamp, updated_at: stamp };
    type Message = { id: number; conversation_id: number; request_id: string; role: string; content: string; sources: object[]; status: string; error: string | null; created_at: string; actions?: AgentToolRun[]; files?: AgentFile[]; page_context?: TimPageContext };
    const conversations: { id: number; project_id: number | null; title: string; created_at: string; updated_at: string }[] = [];
    const messages = new Map<number, Message[]>();
    const control = { projects: [project], answer: "Руководитель проекта — Анна Иванова.", sources: null as KnowledgeSource[] | null, pending: false, fail: false, submissions: [] as Record<string, unknown>[], action: null as AgentToolRun | null, decisions: [] as Record<string, unknown>[] };
    const uploaded: AgentFile = { id: "f918d205-2ced-48eb-85f4-19b8e55ef361", original_name: "acceptance.md", content_type: "text/markdown", size: 12, created_at: stamp };
    let messageId = 0;
    await page.route(/https:\/\/fonts\.(?:googleapis|gstatic)\.com\//, route => route.abort());
    await page.route("**/api/v1/**", async (route) => {
        const path = new URL(route.request().url()).pathname;
        const method = route.request().method();
        const headers = { "access-control-allow-origin": "http://127.0.0.1:4179", "access-control-allow-credentials": "true", "access-control-allow-headers": "content-type", "access-control-allow-methods": "GET, POST, DELETE, OPTIONS" };
        const reply = (json: unknown, status = 200) => route.fulfill({ status, json, headers });
        if (method === "OPTIONS") return route.fulfill({ status: 204, headers });
        if (path.endsWith("/auth/me")) return reply(user);
        if (path.endsWith("/dashboard")) return reply({ totals: { total_projects: 1, total_tasks: 0, active_projects: 1, overdue_tasks: 0, in_progress_tasks: 0, done_tasks: 0 }, projects: [], attention_tasks: [], recent_tasks: [] });
        if (path === "/api/v1/projects") return reply(control.projects);
        const projectPath = path.match(/^\/api\/v1\/projects\/(\d+)$/);
        if (projectPath) return reply(control.projects.find((item) => item.id === Number(projectPath[1])));
        if (path.endsWith("/chat")) return reply({ detail: "Чат недоступен" }, 404);
        if (path.endsWith("/members")) return reply([{ id: 1, project_id: 1, role: "OWNER", user, created_at: stamp }]);
        if (path.endsWith("/knowledge/status")) return reply({ enabled: true, ready: false, points_count: 5, pending_jobs: 0, processing_jobs: 0, failed_jobs: 1, last_error: null, obsolete_points: 0,
            coverage: [{ entity_type: "project", total: 1, indexed: 1, missing: 0, stale: 0 }, { entity_type: "sticker", total: 2, indexed: 1, missing: 1, stale: 0 }, { entity_type: "member", total: 1, indexed: 1, missing: 0, stale: 0 }],
            file_issues: [{ source_id: "attachment:4", title: "scan.pdf", status: "empty", detail: "В файле нет текстового слоя." }] });
        if (path.endsWith("/agent/conversations") || path === "/api/v1/tim/conversations") {
            const projectId = path.startsWith("/api/v1/tim/") ? null : Number(path.match(/projects\/(\d+)/)![1]);
            if (method === "GET") return reply({ items: conversations.filter((item) => item.project_id === projectId).slice().reverse(), next_offset: null });
            const conversation = { id: conversations.length + 1, project_id: projectId, title: "Новый диалог", created_at: stamp, updated_at: stamp };
            conversations.push(conversation);
            messages.set(conversation.id, []);
            return reply(conversation, 201);
        }
        if (/\/(?:agent|tim)\/conversations\/\d+\/files$/.test(path)) return reply(uploaded, 201);
        if (/\/(?:agent|tim)\/conversations\/\d+\/files\//.test(path) && method === "DELETE") return route.fulfill({ status: 204, headers });
        const decisionPath = path.match(/\/(?:agent|tim)\/conversations\/(\d+)\/actions\/([^/]+)\/decision$/);
        if (decisionPath) {
            const body = route.request().postDataJSON();
            control.decisions.push(body);
            const assistant = messages.get(Number(decisionPath[1]))!.at(-1)!;
            const action = assistant.actions!.find((item) => item.id === decisionPath[2])!;
            action.status = body.decision === "approve" ? "completed" : "rejected";
            action.result = { message: action.status === "completed" ? "Задача удалена" : "Действие отклонено" };
            assistant.status = "queued";
            return reply(action);
        }
        const match = path.match(/\/(?:agent|tim)\/conversations\/(\d+)\/messages(?:\/(\d+)\/retry)?$/);
        if (match) {
            const id = Number(match[1]);
            const rows = messages.get(id)!;
            if (match[2]) {
                const message = rows.find((item) => item.id === Number(match[2]))!;
                Object.assign(message, { status: "queued", error: null });
                return reply(message, 202);
            }
            if (method === "GET") {
                const assistant = rows.at(-1);
                if (assistant?.status === "queued" && !control.pending) {
                    Object.assign(assistant, control.fail ? { status: "failed", error: "Не удалось подготовить ответ. Можно повторить запрос." } : {
                        status: "completed", content: control.answer,
                        sources: control.sources ?? [{ source_id: "member:1", entity_type: "member", entity_id: 1, title: "Анна Иванова", excerpt: "Роль: OWNER", score: null, task_id: null, document_slug: null, related_source_ids: ["project:1"] }],
                    });
                    if (control.action && !assistant.actions) assistant.actions = [structuredClone(control.action)];
                }
                return reply({ items: rows, next_before_id: null });
            }
            const data = route.request().postDataJSON();
            control.submissions.push(data);
            const common = { conversation_id: id, request_id: data.request_id, sources: [], error: null, created_at: stamp };
            const question: Message = { ...common, id: ++messageId, role: "user", content: data.content, status: "completed", files: data.file_ids?.length ? [uploaded] : [], ...(data.page_context ? { page_context: data.page_context } : {}) };
            const assistant: Message = { ...common, id: ++messageId, role: "assistant", content: "", status: "queued" };
            rows.push(question, assistant);
            conversations.find((item) => item.id === id)!.title = data.content;
            return reply({ user_message: question, assistant_message: assistant }, 202);
        }
        return reply([]);
    });
    return control;
}

test("чат восстанавливает ожидающий ответ после перезагрузки и открывает источники", async ({ page }) => {
    const control = await installProjectApi(page);
    control.pending = true;
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/projects/DEMO/knowledge");
    await expect(page).toHaveURL(/\/projects\/DEMO\/chat\?view=knowledge$/);
    await expect(page.getByText("1 / 2", { exact: true })).toBeVisible();
    await expect(page.getByText("В файле нет текстового слоя.", { exact: false })).toBeVisible();
    await openProjectPersonal(page);
    await expect(page.getByText("Контекст неполный", { exact: true })).toBeVisible();
    await page.getByRole("textbox", { name: "Вопрос Арису по проекту", exact: true }).fill("Кто руководитель проекта?");
    await page.getByRole("textbox", { name: "Вопрос Арису по проекту", exact: true }).press("Enter");
    await expect(page.getByText("Арис думает…", { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByText("Кто руководитель проекта?", { exact: true }).last()).toBeVisible();
    await expect(page.getByText("Арис думает…", { exact: true })).toBeVisible();
    control.pending = false;
    await expect(page.getByText("Руководитель проекта — Анна Иванова.", { exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByText("Руководитель проекта — Анна Иванова.", { exact: true })).toBeVisible();
    await page.screenshot({ path: "test-results/agent-dialog.png" });
    await page.getByRole("button", { name: "Участник: Анна Иванова", exact: true }).click();
    await expect(page).toHaveURL(/\/projects\/DEMO\/team$/);
    expect(control.submissions).toHaveLength(1);
    expect(Object.keys(control.submissions[0]).sort()).toEqual(["content", "file_ids", "request_id"]);
    expect(errors).toEqual([]);
});

test("новый диалог имеет отдельную историю, прежний разговор можно продолжить", async ({ page }) => {
    const control = await installProjectApi(page);
    await page.goto("/projects/DEMO/agent");
    await expect(page).toHaveURL(/\/projects\/DEMO\/chat\?view=personal$/);
    const input = page.getByRole("textbox", { name: "Вопрос Арису по проекту", exact: true });
    await input.fill("Обсудим тестирование окна заказа");
    await input.press("Enter");
    await expect(page.getByText("Руководитель проекта — Анна Иванова.", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Новый диалог", exact: true }).click();
    await expect(page.getByText("Обсудим тестирование окна заказа", { exact: true }).filter({ visible: true })).toHaveCount(0);
    await input.fill("Обсудим календарь проекта");
    await input.press("Enter");
    await expect(page.getByText("Руководитель проекта — Анна Иванова.", { exact: true })).toBeVisible();
    await page.getByRole("combobox", { name: "Диалог" }).selectOption("1");
    await expect(page.locator("p", { hasText: "Обсудим тестирование окна заказа" })).toBeVisible();
    await expect(page.locator("p", { hasText: "Обсудим календарь проекта" })).toHaveCount(0);
    await page.reload();
    await input.fill("Продолжим с того же места");
    await input.press("Enter");
    await expect(page.locator("p", { hasText: "Продолжим с того же места" })).toBeVisible();
    expect(control.submissions).toHaveLength(3);
});

test("ошибка ответа сохраняется и повтор не дублирует вопрос", async ({ page }) => {
    const control = await installProjectApi(page);
    control.fail = true;
    await page.goto("/projects/DEMO/chat?view=personal");
    await page.getByRole("textbox", { name: "Вопрос Арису по проекту", exact: true }).fill("Расскажи о проекте");
    await page.getByRole("textbox", { name: "Вопрос Арису по проекту", exact: true }).press("Enter");
    await expect(page.getByRole("button", { name: "Повторить ответ" })).toBeVisible();
    await page.reload();
    control.fail = false;
    await page.getByRole("button", { name: "Повторить ответ" }).click();
    await expect(page.getByText("Руководитель проекта — Анна Иванова.", { exact: true })).toBeVisible();
    await expect(page.locator("p", { hasText: "Расскажи о проекте" })).toHaveCount(1);
    expect(control.submissions).toHaveLength(1);
});

for (const decision of ["approve", "reject"] as const) {
    test(`карточка действия восстанавливается и сохраняет решение ${decision}`, async ({ page }) => {
        const control = await installProjectApi(page);
        control.action = { id: "5eea642e-3196-45c0-ac85-1286b0d3a81c", tool_name: "delete_task", title: "Удалить задачу",
            arguments: { task_id: 42, expected_updated_at: "2026-09-21T10:00:00Z" }, status: "pending",
            result: { preview: { task: { key: "DEMO-42", title: "Проверка окна заказа" } } }, created_at: "2026-09-21T10:00:00Z" };
        await page.goto("/projects/DEMO/chat?view=personal");
        const input = page.getByRole("textbox", { name: "Вопрос Арису по проекту", exact: true });
        await input.fill("Удали задачу DEMO-42");
        await input.press("Enter");
        const card = page.getByRole("region", { name: "Действие: Удалить задачу" });
        await expect(card.getByText("Ожидает решения", { exact: true })).toBeVisible();
        await page.reload();
        await expect(card.getByText("Проверка окна заказа", { exact: true })).toBeVisible();
        await card.getByRole("button", { name: decision === "approve" ? "Подтвердить" : "Отклонить", exact: true }).click();
        await expect(card.getByText(decision === "approve" ? "Выполнено" : "Отклонено", { exact: true })).toBeVisible();
        await page.reload();
        await expect(card.getByRole("button", { name: "Подтвердить", exact: true })).toHaveCount(0);
        expect(control.decisions).toEqual([{ decision }]);
    });
}

test("файл загружается в личный диалог и сохраняется вместе с вопросом", async ({ page }) => {
    const control = await installProjectApi(page);
    await page.goto("/projects/DEMO/chat?view=personal");
    await expect(page.getByRole("button", { name: "Добавить файл до 10 МБ", exact: true })).toBeEnabled();
    await page.getByLabel("Файлы для Ариса", { exact: true }).setInputFiles({ name: "acceptance.md", mimeType: "text/markdown", buffer: Buffer.from("Проверка окна") });
    await expect(page.getByRole("button", { name: "Убрать acceptance.md" })).toBeVisible();
    await page.getByRole("textbox", { name: "Вопрос Арису по проекту", exact: true }).fill("Прикрепи файл к задаче DEMO-42");
    await page.getByRole("textbox", { name: "Вопрос Арису по проекту", exact: true }).press("Enter");
    await expect(page.getByRole("button", { name: "Убрать acceptance.md" })).toHaveCount(0);
    await page.reload();
    await expect(page.locator("p", { hasText: "acceptance.md" })).toBeVisible();
    expect(control.submissions[0].file_ids).toEqual(["f918d205-2ced-48eb-85f4-19b8e55ef361"]);
});
