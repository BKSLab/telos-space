import { expect, test, type Locator, type Page } from "@playwright/test";
import type { AgentToolRun, AgentFile, KnowledgeSource, AgentPageContext } from "../src/lib/types";

async function expectAgentCardAccent(page: Page, card: Locator) {
    const stripe = await card.evaluate(element => {
        const style = getComputedStyle(element, "::before");
        return { image: style.backgroundImage, width: Number.parseFloat(style.width), height: Number.parseFloat(style.height), opacity: Number(style.opacity) };
    });
    expect(stripe.image).toContain("linear-gradient");
    expect(stripe.image).toMatch(/rgb\(0, 200, 244\)/);
    expect(stripe.image).toMatch(/rgb\(188, 103, 239\)/);
    expect(stripe.width).toBeGreaterThan(0);
    expect(stripe.height).toBeGreaterThan(0);
    expect(stripe.opacity).toBeGreaterThan(0);
    await card.focus();
    await page.keyboard.press("Tab");
    await page.keyboard.press("Shift+Tab");
    await expect(card).toBeFocused();
    const paint = await card.evaluate(element => {
        const style = getComputedStyle(element);
        return { image: style.backgroundImage, outline: Number.parseFloat(style.outlineWidth), focusVisible: element.matches(":focus-visible") };
    });
    expect(paint.image.match(/linear-gradient/g)!.length).toBeGreaterThanOrEqual(3);
    expect(paint.outline).toBeGreaterThanOrEqual(2);
    expect(paint.focusVisible).toBe(true);
    return { image: paint.image, stripe: stripe.image };
}

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
    await expect(page).toHaveURL(new RegExp(`/projects/${key}/conversations(?:\\?conversation=\\d+)?$`));
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
    await expect(page.getByRole("button", { name: "Приложить файл для Ариса: до 5 штук, до 10 МБ каждый", exact: true })).toBeEnabled();
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

test("карточка задачи показывает поля и фирменный акцент в личном окне и вкладке", async ({ page }, info) => {
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    const control = await installProjectApi(page);
    control.answer = "";
    control.sources = [{ source_id: "task:42", entity_type: "task", entity_id: 42, title: "DEMO-42 · Проверить оплату", task_id: 42, score: null, excerpt: null,
        card: { key: "DEMO-42", status: "В работе", priority: "HIGH", assignee: "Анна Иванова", due_date: "2026-10-01", summary: "Проверить оплату картой и возврат средств." } }];
    control.action = { id: "create-42", tool_name: "telos_create_task", title: "Создать задачу", arguments: { title: "Проверить оплату" }, status: "completed", result: { source_id: "task:42", task: { id: 42, key: "DEMO-42", title: "Проверить оплату" } }, created_at: "2026-09-22T10:00:00Z" };
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
    const floatingPaint = await expectAgentCardAccent(page, card);
    await page.screenshot({ path: info.outputPath("floating-agent-card.png"), animations: "disabled" });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(card).toBeVisible();
    const panel = await page.getByRole("dialog", { name: "Чаты", exact: true }).boundingBox();
    expect(panel!.x).toBeGreaterThanOrEqual(0);
    expect(panel!.x + panel!.width).toBeLessThanOrEqual(390);
    await expectAgentCardAccent(page, card);
    await page.screenshot({ path: info.outputPath("floating-agent-mobile.png"), animations: "disabled" });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole("button", { name: "Открыть чат во вкладке" }).click();
    await expect(page).toHaveURL(/\/chat$/);
    await expect(card).toBeVisible();
    expect(await expectAgentCardAccent(page, card), "Один акцент сохраняется при переходе личного чата во вкладку").toEqual(floatingPaint);
    await page.screenshot({ path: info.outputPath("personal-agent-card.png"), animations: "disabled" });
    expect(errors).toEqual([]);
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
    await expect(page).toHaveURL(/\/projects\/DEMO\/conversations$/);
    await expect(page.getByRole("button", { name: "Открыть чаты", exact: true })).toHaveCount(0);
    await page.reload();
    await expect(input).toHaveValue("Черновик первого проекта");
});

async function installProjectApi(page: Page) {
    const stamp = "2026-09-21T10:00:00Z";
    const user = { id: 1, username: "anna", first_name: "Анна", last_name: "Иванова", middle_name: null, is_active: true, has_avatar: false };
    const project = { id: 1, owner_id: 1, key: "DEMO", name: "Знания проекта", description_md: "Паспорт", description_sections: null, due_date_has_been_set: false, status: "ACTIVE", color: "#7299cc", icon: null, start_date: null, due_date: null, order_index: 0, created_at: stamp, updated_at: stamp };
    type Message = { id: number; conversation_id: number; request_id: string; role: string; content: string; sources: object[]; status: string; error: string | null; created_at: string; actions?: AgentToolRun[]; files?: AgentFile[]; page_context?: AgentPageContext };
    const conversations: { id: number; project_id: number | null; title: string; created_at: string; updated_at: string }[] = [];
    const messages = new Map<number, Message[]>();
    const control = { projects: [project], answer: "Руководитель проекта — Анна Иванова.", sources: null as KnowledgeSource[] | null, pending: false, fail: false, submissions: [] as Record<string, unknown>[], attempts: [] as { path: string; body: Record<string, unknown> }[], dropResponses: 0, action: null as AgentToolRun | null, decisions: [] as Record<string, unknown>[] };
    const uploaded: AgentFile = { id: "f918d205-2ced-48eb-85f4-19b8e55ef361", original_name: "acceptance.md", content_type: "text/markdown", size: 12, created_at: stamp };
    let messageId = 0;
    await page.route("https://fonts.googleapis.com/**", route => route.fulfill({ status: 200, contentType: "text/css", body: "" }));
    await page.route("**/api/v1/**", async (route) => {
        const path = new URL(route.request().url()).pathname;
        const method = route.request().method();
        const headers = { "access-control-allow-origin": "http://127.0.0.1:4179", "access-control-allow-credentials": "true", "access-control-allow-headers": "content-type", "access-control-allow-methods": "GET, POST, DELETE, OPTIONS" };
        const reply = (json: unknown, status = 200) => route.fulfill({ status, json, headers });
        if (method === "OPTIONS") return route.fulfill({ status: 204, headers });
        if (path.endsWith("/auth/me")) return reply(user);
        if (path === "/api/v1/documents") return reply({ items: [], total: 0, limit: 20, offset: 0, next_offset: null });
        if (path.endsWith("/dashboard")) return reply({ totals: { total_projects: 1, total_tasks: 0, active_projects: 1, overdue_tasks: 0, in_progress_tasks: 0, done_tasks: 0 }, projects: [], attention_tasks: [], recent_tasks: [] });
        if (path === "/api/v1/projects") return reply(control.projects);
        const projectPath = path.match(/^\/api\/v1\/projects\/(\d+)$/);
        if (projectPath) return reply(control.projects.find((item) => item.id === Number(projectPath[1])));
        if (path.endsWith("/chat")) return reply({ detail: "Чат недоступен" }, 404);
        if (path.endsWith("/members")) return reply([{ id: 1, project_id: 1, role: "OWNER", user, created_at: stamp }]);
        if (path.endsWith("/knowledge/status")) return reply({ enabled: true, ready: false, points_count: 5, pending_jobs: 0, processing_jobs: 0, failed_jobs: 1, last_error: null, obsolete_points: 0,
            coverage: [{ entity_type: "project", total: 1, indexed: 1, missing: 0, stale: 0 }, { entity_type: "sticker", total: 2, indexed: 1, missing: 1, stale: 0 }, { entity_type: "member", total: 1, indexed: 1, missing: 0, stale: 0 }],
            file_issues: [{ source_id: "attachment:4", title: "scan.pdf", status: "empty", detail: "В файле нет текстового слоя." }] });
        if (path.endsWith("/aris/conversations") || path === "/api/v1/aris/conversations") {
            const projectId = path.startsWith("/api/v1/aris/") ? null : Number(path.match(/projects\/(\d+)/)![1]);
            if (method === "GET") return reply({ items: conversations.filter((item) => item.project_id === projectId).slice().reverse(), next_offset: null });
            const conversation = { id: conversations.length + 1, project_id: projectId, title: "Новый диалог", created_at: stamp, updated_at: stamp };
            conversations.push(conversation);
            messages.set(conversation.id, []);
            return reply(conversation, 201);
        }
        if (/\/aris\/conversations\/\d+\/files$/.test(path)) return reply(uploaded, 201);
        if (/\/aris\/conversations\/\d+\/files\//.test(path) && method === "DELETE") return route.fulfill({ status: 204, headers });
        const decisionPath = path.match(/\/aris\/conversations\/(\d+)\/actions\/([^/]+)\/decision$/);
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
        const match = path.match(/\/aris\/conversations\/(\d+)\/messages(?:\/(\d+)\/retry)?$/);
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
                        sources: control.sources ?? [{ source_id: "member:1", entity_type: "member", entity_id: 1, title: "Анна Иванова", excerpt: "Роль: OWNER", score: null, task_id: null, related_source_ids: ["project:1"] }],
                    });
                    if (control.action && !assistant.actions) assistant.actions = [structuredClone(control.action)];
                }
                return reply({ items: rows, next_before_id: null });
            }
            const data = route.request().postDataJSON();
            control.attempts.push({ path, body: data });
            const previous = rows.find(message => message.role === "user" && message.request_id === data.request_id);
            if (previous) {
                if (control.dropResponses > 0) { control.dropResponses -= 1; return route.abort("failed"); }
                return reply({ user_message: previous, assistant_message: rows.find(message => message.role === "assistant" && message.request_id === data.request_id) }, 202);
            }
            control.submissions.push(data);
            const common = { conversation_id: id, request_id: data.request_id, sources: [], error: null, created_at: stamp };
            const question: Message = { ...common, id: ++messageId, role: "user", content: data.content, status: "completed", files: data.file_ids?.length ? [uploaded] : [], ...(data.page_context ? { page_context: data.page_context } : {}) };
            const assistant: Message = { ...common, id: ++messageId, role: "assistant", content: "", status: "queued" };
            rows.push(question, assistant);
            conversations.find((item) => item.id === id)!.title = data.content;
            if (control.dropResponses > 0) { control.dropResponses -= 1; return route.abort("failed"); }
            return reply({ user_message: question, assistant_message: assistant }, 202);
        }
        return reply([]);
    });
    return control;
}

test("чат восстанавливает ожидающий ответ после перезагрузки и открывает источники", async ({ page }, info) => {
    const control = await installProjectApi(page);
    control.pending = true;
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/projects/DEMO/knowledge");
    await expect(page).toHaveURL(/\/projects\/DEMO\/knowledge$/);
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
    const sourceCard = page.getByRole("button", { name: "Участник: Анна Иванова", exact: true });
    await expectAgentCardAccent(page, sourceCard);
    await page.screenshot({ path: info.outputPath("project-conversation-source.png"), animations: "disabled" });
    await sourceCard.press("Enter");
    await expect(page).toHaveURL(/\/projects\/DEMO\/team$/);
    expect(control.submissions).toHaveLength(1);
    expect(Object.keys(control.submissions[0]).sort()).toEqual(["content", "file_ids", "request_id"]);
    expect(errors).toEqual([]);
});

test("новый диалог имеет отдельную историю, прежний разговор можно продолжить", async ({ page }) => {
    const control = await installProjectApi(page);
    await page.goto("/projects/DEMO/conversations");
    await expect(page).toHaveURL(/\/projects\/DEMO\/conversations$/);
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
    await page.goto("/projects/DEMO/conversations");
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
        control.action = { id: "5eea642e-3196-45c0-ac85-1286b0d3a81c", tool_name: "telos_delete_task", title: "Удалить задачу",
            arguments: { task_id: 42, expected_updated_at: "2026-09-21T10:00:00Z" }, status: "pending",
            result: { preview: { task: { key: "DEMO-42", title: "Проверка окна заказа" } } }, created_at: "2026-09-21T10:00:00Z" };
        await page.goto("/projects/DEMO/conversations");
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
    await page.goto("/projects/DEMO/conversations");
    await expect(page.getByRole("button", { name: "Добавить файл до 10 МБ", exact: true })).toBeEnabled();
    await page.getByLabel("Файлы для Ариса", { exact: true }).setInputFiles({ name: "acceptance.md", mimeType: "text/markdown", buffer: Buffer.from("Проверка окна") });
    await expect(page.getByRole("button", { name: "Убрать acceptance.md" })).toBeVisible();
    await page.getByRole("textbox", { name: "Вопрос Арису по проекту", exact: true }).fill("Прикрепи файл к задаче DEMO-42");
    await page.getByRole("textbox", { name: "Вопрос Арису по проекту", exact: true }).press("Enter");
    await expect(page.getByRole("button", { name: "Убрать acceptance.md" })).toHaveCount(0);
    await page.reload();
    await expect(page.getByRole("link", { name: "Скачать файл acceptance.md" })).toBeVisible();
    expect(control.submissions[0].file_ids).toEqual(["f918d205-2ced-48eb-85f4-19b8e55ef361"]);
});

for (const scope of [
    { name: "личный", route: "/chat", input: "Вопрос Арису", error: "Арис сейчас недоступен", prefix: "/api/v1/aris/conversations/" },
    { name: "проектный", route: "/projects/DEMO/conversations?task=42#retry", input: "Вопрос Арису по проекту", error: "Не удалось обновить диалог", prefix: "/api/v1/projects/1/aris/conversations/" },
]) {
    test(`${scope.name} запрос сохраняет UUID после потери ответа и перезагрузки`, async ({ page }) => {
        const control = await installProjectApi(page);
        control.dropResponses = 2;
        await page.goto(scope.route);
        const input = page.getByRole("textbox", { name: scope.input, exact: true });
        await input.fill("Проверь план после потери ответа");
        await input.press("Enter");
        await expect(page.getByText(scope.error, { exact: true })).toBeVisible();
        expect(control.attempts).toHaveLength(2);
        const requestId = control.attempts[0].body.request_id;
        expect(requestId).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
        expect(control.attempts[1].body).toEqual(control.attempts[0].body);
        await page.reload();
        await expect(input).toHaveValue("Проверь план после потери ответа");
        await expect(input).toBeEnabled();
        await input.press("Enter");
        await expect(input).toHaveValue("");
        expect(control.attempts).toHaveLength(3);
        expect(control.attempts.every(attempt => attempt.path.startsWith(scope.prefix) && attempt.body.request_id === requestId)).toBe(true);
        expect(control.submissions).toHaveLength(1);
        await expect(page.locator("p", { hasText: "Проверь план после потери ответа" }).filter({ visible: true })).toHaveCount(1);
        if (scope.name === "проектный") {
            const url = new URL(page.url());
            expect(url.pathname).toBe("/projects/DEMO/conversations");
            expect(url.searchParams.get("task")).toBe("42");
            expect(url.hash).toBe("#retry");
        }
    });
}
