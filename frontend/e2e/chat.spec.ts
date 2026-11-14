import { expect, test, type Locator, type Page, type WebSocketRoute } from "@playwright/test";
import { createHash, randomUUID } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import type { ChatAgentState, ChatAttachment, ChatEntity, ChatEvent, ChatMessage, ChatMessageCreate } from "../src/lib/projectChat";
import type { DocumentDetail, KnowledgeSource, Task } from "../src/lib/types";

test.setTimeout(60_000);

const browserErrors = new WeakMap<Page, { kind: string; text: string; expected: boolean }[]>();

function watchBrowserErrors(page: Page, allowAccessErrors = false) {
    const errors: { kind: string; text: string; expected: boolean }[] = [];
    browserErrors.set(page, errors);
    page.on("pageerror", error => errors.push({ kind: "pageerror", text: error.message, expected: false }));
    page.on("console", entry => {
        if (entry.type() !== "error") return;
        const text = entry.text();
        errors.push({ kind: "console", text, expected: allowAccessErrors && /Failed to load resource: the server responded with a status of (403|404)/.test(text) });
    });
}

async function expectBrandPaint(target: Locator, pseudo: "::before" | "::after" | null = null) {
    const paint = await target.evaluate((element, pseudoElement) => {
        const style = getComputedStyle(element, pseudoElement);
        return { image: style.backgroundImage, opacity: Number(style.opacity), display: style.display, width: style.width, height: style.height };
    }, pseudo);
    expect(paint.image, "Видимый градиент содержит цвета бренда").toContain("linear-gradient");
    expect(paint.image).toMatch(/rgba?\(0, 200, 244(?:,|\))/);
    expect(paint.image).toMatch(/rgba?\(188, 103, 239(?:,|\))/);
    expect(paint.opacity).toBeGreaterThan(0);
    expect(paint.display).not.toBe("none");
    if (pseudo) {
        expect(Number.parseFloat(paint.width)).toBeGreaterThan(0);
        expect(Number.parseFloat(paint.height)).toBeGreaterThan(0);
    }
}

async function focusCardWithKeyboard(page: Page, card: Locator) {
    await card.focus();
    await page.keyboard.press("Tab");
    await page.keyboard.press("Shift+Tab");
    await expect(card).toBeFocused();
    const focus = await card.evaluate(element => ({ visible: element.matches(":focus-visible"), width: Number.parseFloat(getComputedStyle(element).outlineWidth), style: getComputedStyle(element).outlineStyle }));
    expect(focus.visible).toBe(true);
    expect(focus.width).toBeGreaterThanOrEqual(2);
    expect(focus.style).not.toBe("none");
}

async function expectTaskDialogThenClose(page: Page, task: Task, screenshotPath?: string) {
    const dialog = page.getByRole("dialog", { name: /^Задача/ });
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveAccessibleName(`Задача ${task.key}`);
    await expect(dialog.getByText(task.key, { exact: true })).toBeVisible();
    await expect(dialog.getByText(task.title, { exact: true })).toBeVisible();
    await expect(dialog.getByText(task.description_md!, { exact: true })).toBeVisible();
    await expect(dialog.getByRole("tablist", { name: "Разделы задачи", exact: true })).toBeVisible();
    await expect(page.locator(".modal-workspace").filter({ has: dialog })).toBeVisible();
    if (screenshotPath) await page.screenshot({ path: screenshotPath, animations: "disabled" });
    await dialog.getByRole("button", { name: "Закрыть задачу", exact: true }).click();
    await expect(dialog).toHaveCount(0);
}

test.afterEach(async ({ page }, info) => {
    const errors = browserErrors.get(page);
    if (!errors) return;
    const path = info.outputPath("browser-errors.json");
    await mkdir(info.outputDir, { recursive: true });
    await writeFile(path, JSON.stringify(errors, null, 2), "utf-8");
    await info.attach("browser-errors", { path, contentType: "application/json" });
    expect(errors.filter(error => !error.expected), "Нет непредвиденных console.error и pageerror").toEqual([]);
});

/** Общий серверный state для двух браузерных участников и нескольких вкладок. */
function chatServer() {
    const stamp = "2026-09-21T10:00:00Z";
    const members = [{ id: 1, username: "anna", display_name: "Анна Иванова" }, { id: 2, username: "oleg", display_name: "Олег Петров" }];
    const project = { id: 1, owner_id: 1, key: "DEMO", name: "Портал заказов", description_md: "Командная работа над интерфейсом", description_sections: null, due_date_has_been_set: false, status: "ACTIVE", color: "#7299cc", icon: null, start_date: null, due_date: null, order_index: 0, created_at: stamp, updated_at: stamp };
    const rows: ChatMessage[] = [];
    const events: ChatEvent[] = [];
    const sockets = new Map<WebSocketRoute, number>();
    const reads = new Map<number, number>();
    const files = new Map<string, ChatAttachment>();
    const entity: ChatEntity = { entity_type: "DOCUMENT", entity_id: null, document_ref: "d1_ZG9jdW1lbnQ6MQ", title: "План тестирования", available: true, status: null, subtitle: null, href: "/projects/DEMO/docs/d1_ZG9jdW1lbnQ6MQ" };
    const tasks = new Map<number, Task>([42, 43].map((id): [number, Task] => [id, {
        id, revision: 1, project_id: 1, stage_id: 1, number: id, key: `DEMO-${id}`,
        title: id === 42 ? "Проверить форму заказа" : "Согласовать мобильную версию",
        description_md: id === 42 ? "Проверить обязательные поля и сохранение заказа." : "Согласовать расположение кнопок на телефоне.",
        priority: "MEDIUM", role: null, assignee: "Анна Иванова", participants: [],
        start_date: null, due_date: "2026-11-20", baseline_start_date: null, baseline_due_date: null,
        completed_at: null, position: id, created_at: stamp, updated_at: stamp, comments_count: 0,
        last_comment: null, search_match_source: null, search_title: null, search_excerpt: null, wbs_node_id: null,
    }]));
    const makeDocument = (ref: string, title: string, source_kind: "text" | "upload" = "text"): DocumentDetail => ({
        ref, title, source_kind, caption: null, author: { id: 1, name: "Анна Иванова" },
        ownership: { kind: "project", project_id: 1, owner_task_id: null }, tasks: [], created_at: stamp, updated_at: stamp,
        revision: 1, format: source_kind === "text" ? "md" : "txt", original: null,
        text: { available: true, total_chars: 0, rendition: source_kind === "text" ? "markdown" : "extracted_text", content_hash: "", url: `/api/v1/documents/${ref}/text` },
        states: { extraction: { status: "ready", detail: null }, fts: { status: "ready", detail: null }, semantic: { status: "ready", detail: null } },
        capabilities: { can_read: true, can_edit: false, can_delete: false, can_retry: false, can_link: false, can_detach: false },
    });
    const document = { ...makeDocument(entity.document_ref!, entity.title), content_md: "Критерии готовности интерфейса." };
    const projectFileText = "Согласованный бюджет проекта — 742000 рублей.";
    const projectFile = { ...makeDocument("d1_cHJvamVjdF9maWxlOjk", "Бюджет.txt", "upload"), rawText: projectFileText };
    const attachment = { ...makeDocument("d1_YXR0YWNobWVudDox", "Требования.md", "upload"), rawText: "😀 Начало.\n\n" + "Общий контекст. ".repeat(700) + "Согласованное условие: оплата после приёмки." };
    attachment.ownership = { kind: "task", project_id: 1, owner_task_id: 42 };
    const textHash = (text: string) => createHash("sha256").update(text, "utf8").digest("hex");
    const taskEntity = (id: number): ChatEntity => ({ entity_type: "TASK", entity_id: id, title: tasks.get(id)!.title, available: true, status: "В работе", subtitle: `DEMO-${id}`, href: null });
    const taskSource = (id: number): KnowledgeSource => ({ source_id: `task:${id}`, entity_type: "task", entity_id: id, title: `DEMO-${id} · ${tasks.get(id)!.title}`, excerpt: null, score: null, task_id: id });
    const documentSource: KnowledgeSource = { source_id: "document:1", entity_type: "document", entity_id: 1, title: document.title, excerpt: null, score: null, task_id: null, document_ref: document.ref };
    const fileSource: KnowledgeSource = { source_id: "project_file:9", entity_type: "project_file", entity_id: 9, title: projectFile.title, excerpt: null, score: null, task_id: null, document_ref: projectFile.ref,
        reference: { document_ref: projectFile.ref, rendition: "extracted_text", content_hash: textHash(projectFile.rawText), start: 0, end: Array.from(projectFile.rawText).length, quote: projectFile.rawText, offset_unit: "unicode_codepoints" } };
    const attachmentQuote = "Согласованное условие: оплата после приёмки.";
    const attachmentStart = Array.from(attachment.rawText).length - Array.from(attachmentQuote).length;
    const attachmentSource: KnowledgeSource = { source_id: "attachment:1", entity_type: "attachment", entity_id: 1, title: attachment.title, excerpt: null, score: null, task_id: 42, document_ref: attachment.ref,
        reference: { document_ref: attachment.ref, rendition: "extracted_text", content_hash: textHash(attachment.rawText), start: attachmentStart, end: attachmentStart + Array.from(attachmentQuote).length, quote: attachmentQuote, offset_unit: "unicode_codepoints" } };
    let seq = 0;
    let messageId = 0;
    const control = { dropNextAck: false, connections: 0, messageGets: 0, blocked: false, sourceStatus: new Map<string, number>(), commands: [] as { type: string; data: Record<string, unknown> }[] };
    const emit = (type: string, data: ChatEvent["data"], broadcast = true) => {
        const event = { type, project_id: 1, seq: ++seq, data: structuredClone(data) };
        events.push(event);
        if (broadcast) for (const socket of sockets.keys()) socket.send(JSON.stringify(event));
        return event;
    };
    const create = (userId: number, data: ChatMessageCreate, broadcast = true, patch: Partial<ChatMessage> = {}) => {
        const duplicate = rows.find((row) => row.author?.id === userId && row.client_message_id === data.client_message_id);
        if (duplicate) return duplicate;
        const original = rows.find((row) => row.id === data.reply_to_message_id);
        const message: ChatMessage = { id: ++messageId, chat_id: 1, project_id: 1, seq: seq + 1, author_type: "USER", author: members.find((user) => user.id === userId)!, client_message_id: data.client_message_id, content: data.content, revision: 1, created_at: stamp, edited_at: null, deleted_at: null, reply: original ? { id: original.id, seq: original.seq, author_type: original.author_type, author: original.author, content: original.content, deleted: Boolean(original.deleted_at) } : null, mentions: members.filter((user) => data.mentions.includes(user.id)), entities: data.entities.length ? [structuredClone(entity)] : [], attachments: data.attachment_ids.map((id) => ({ ...files.get(id)!, message_id: messageId })), reactions: [], ...patch };
        rows.push(message); emit("message.created", { message_id: message.id, message }, broadcast);
        return message;
    };
    const add = (content: string, userId = 2, broadcast = true, patch: Partial<ChatMessage> = {}) => create(userId, { content, mentions: [], entities: [], attachment_ids: [], client_message_id: randomUUID(), reply_to_message_id: null }, broadcast, patch);
    const addAris = (content: string, status: ChatAgentState["status"] = "completed", sources: KnowledgeSource[] = [], broadcast = true) => create(0, { content, mentions: [], entities: [], attachment_ids: [], client_message_id: randomUUID(), reply_to_message_id: null }, broadcast, { author_type: "AGENT", author: null, agent: { name: "Арис", status, requester: members[0], request_message_id: 1, error: null, sources, actions: [] } });

    const attach = async (page: Page, userId = 1) => {
        const publicUser = members.find((user) => user.id === userId)!;
        const user = { ...publicUser, first_name: userId === 1 ? "Анна" : "Олег", last_name: userId === 1 ? "Иванова" : "Петров", middle_name: null, is_active: true, has_avatar: false };
        // Сценарий не зависит от доступности внешнего CDN шрифтов.
        await page.route("https://fonts.googleapis.com/**", route => route.fulfill({ status: 200, contentType: "text/css", body: "" }));
        await page.route("**/api/v1/**", async (route) => {
            const url = new URL(route.request().url());
            const path = url.pathname;
            const method = route.request().method();
            const headers = { "access-control-allow-origin": "http://127.0.0.1:4179", "access-control-allow-credentials": "true", "access-control-allow-headers": "content-type", "access-control-allow-methods": "GET, POST, PATCH, PUT, DELETE, OPTIONS" };
            const reply = (json: unknown, status = 200) => route.fulfill({ status, json, headers });
            if (method === "OPTIONS") return route.fulfill({ status: 204, headers });
            if (path.endsWith("/auth/me")) return reply(user);
            if (path.endsWith("/dashboard")) return reply({ totals: { total_projects: 1, total_tasks: 0, active_projects: 1, overdue_tasks: 0, in_progress_tasks: 0, done_tasks: 0 }, projects: [], attention_tasks: [], recent_tasks: [] });
            if (path === "/api/v1/projects") return reply([project]);
            if (path === "/api/v1/projects/1") return reply(project);
            if (path.endsWith("/members")) return reply(members.map((member) => ({ id: member.id, project_id: 1, role: member.id === 1 ? "OWNER" : "MEMBER", user: { ...user, ...member }, created_at: stamp })));
            if (path.endsWith("/stats")) return reply({ total_tasks: 6, in_progress_tasks: 2, done_tasks: 2, overdue_tasks: 0, completion_rate: 0.33 });
            if (path === "/api/v1/projects/1/risks") return reply({ items: [], total: 0, page: 1, page_size: 5 });
            if (path === "/api/v1/users/me/task-attention") return reply({ new_tasks: 0, overdue_tasks: 0, updated_tasks: 0, total: 0, items: [] });
            if (path === "/api/v1/projects/1/stages") return reply([{ id: 1, project_id: 1, name: "В работе", color: "#7299cc", order_index: 0, is_done_stage: false }]);
            const taskMatch = path.match(/^\/api\/v1\/tasks\/(\d+)$/);
            if (taskMatch) {
                const id = Number(taskMatch[1]);
                const status = control.sourceStatus.get(`task:${id}`) ?? (tasks.has(id) ? 200 : 404);
                return status === 200 ? reply(tasks.get(id)) : reply({ detail: "Объект недоступен" }, status);
            }
            if (/^\/api\/v1\/tasks\/\d+\/attention$/.test(path)) return reply({ revision: 1, deadline_revision: 1, overdue: false });
            if (path === "/api/v1/projects/1/wbs") return reply({ nodes: [], tasks: [], task_edges: [], revision: 0 });
            if (path === "/api/v1/documents") return reply({ items: [], total: 0, limit: 20, offset: 0, next_offset: null });
            if (path.endsWith("/chat")) return control.blocked ? reply({ detail: "Чат недоступен" }, 404) : reply({ id: 1, project_id: 1, event_cursor: seq, last_read_seq: reads.get(userId) ?? 0, unread_count: rows.filter((row) => row.author?.id !== userId && row.seq > (reads.get(userId) ?? 0) && !row.deleted_at).length, members });
            if (path.endsWith("/chat/events")) {
                const after = Number(url.searchParams.get("after") ?? 0);
                const selected = events.filter((event) => event.seq! > after).map((event) => ({ ...event, data: { ...event.data, ...(event.data.message_id ? { message: rows.find((row) => row.id === event.data.message_id) } : {}) } }));
                return reply({ events: selected, cursor: seq, has_more: false });
            }
            if (path.endsWith("/chat/messages")) {
                control.messageGets += 1;
                const before = Number(url.searchParams.get("before")) || Infinity;
                const around = Number(url.searchParams.get("around"));
                const all = rows.filter((row) => row.seq < before);
                const index = around ? all.findIndex((row) => row.id === around) : all.length;
                const selected = around ? all.slice(Math.max(0, index - 5), index + 6) : all.slice(-50);
                return reply({ messages: selected, has_more: all.length > selected.length && (around ? index > 5 : true), event_cursor: seq });
            }
            if (path.endsWith("/chat/search")) return reply({ messages: rows.filter((row) => !row.deleted_at && row.content.toLowerCase().includes((url.searchParams.get("q") ?? "").toLowerCase())).slice(-30), has_more: false, event_cursor: seq });
            if (path.endsWith("/chat/entities")) return reply([entity]);
            if (path.endsWith("/chat/entities/resolve")) {
                const refs = route.request().postDataJSON() as { entity_type: string; entity_id: number; document_ref?: string }[];
                return reply(refs.flatMap(ref => ref.entity_type === "TASK" && tasks.has(ref.entity_id) ? [taskEntity(ref.entity_id)] : ref.entity_type === "DOCUMENT" && ref.document_ref === entity.document_ref ? [entity] : []));
            }
            if (path.endsWith("/chat/attachments") && method === "POST") {
                const file: ChatAttachment = { id: randomUUID(), original_name: "acceptance.txt", size_bytes: 12, content_type: "text/plain", message_id: null, created_at: stamp };
                files.set(file.id, file); return reply(file, 201);
            }
            if (path.includes("/chat/attachments/") && method === "DELETE") return route.fulfill({ status: 204, headers });
            const documentPath = path.match(/^\/api\/v1\/documents\/([^/]+)(\/text)?$/);
            if (documentPath) {
                const ref = decodeURIComponent(documentPath[1]);
                const item = ref === document.ref ? document : ref === projectFile.ref ? projectFile : ref === attachment.ref ? attachment : null;
                const sourceKey = ref === document.ref ? "document:testing" : ref === projectFile.ref ? "project_file:9" : "attachment:1";
                const status = control.sourceStatus.get(sourceKey) ?? (item ? 200 : 404);
                if (status !== 200 || !item) return reply({ detail: "Объект недоступен" }, status);
                const raw = "content_md" in item ? item.content_md : item.rawText;
                const points = Array.from(raw); const content_hash = textHash(raw);
                if (documentPath[2]) {
                    const offset = Number(url.searchParams.get("offset") ?? 0); const end = Math.min(points.length, offset + Number(url.searchParams.get("max_chars") ?? 8000));
                    return reply({ ref, text: points.slice(offset, end).join(""), rendition: item.text.rendition, content_hash, offset, next_offset: end < points.length ? end : null, total_chars: points.length, offset_unit: "unicode_codepoints" });
                }
                return reply({ ...item, content_md: undefined, rawText: undefined, text: { ...item.text, total_chars: points.length, content_hash } });
            }
            return reply([]);
        });
        await page.routeWebSocket("**/users/me/task-attention/ws", socket => {
            socket.send(JSON.stringify({ type: "task-attention", data: { new_tasks: 0, overdue_tasks: 0, updated_tasks: 0, total: 0, items: [] } }));
        });
        await page.routeWebSocket("**/projects/1/chat/ws", (socket) => {
            control.connections += 1; sockets.set(socket, userId);
            socket.onClose(() => { sockets.delete(socket); });
            if (control.blocked) { void socket.close({ code: 4403 }); return; }
            socket.send(JSON.stringify({ type: "ready", project_id: 1, data: { heartbeat_seconds: 20, can_write: true, presence: [1, 2], typing: [] } }));
            socket.onMessage((raw) => {
                const command = JSON.parse(String(raw));
                control.commands.push(command);
                let result: object = {};
                if (command.type === "message.send") {
                    const message = create(userId, command.data, !control.dropNextAck);
                    if (control.dropNextAck) { control.dropNextAck = false; sockets.delete(socket); void socket.close({ code: 1013 }); return; }
                    result = { message, client_message_id: message.client_message_id };
                } else if (command.type === "message.edit") {
                    const message = rows.find((row) => row.id === command.message_id)!;
                    Object.assign(message, { content: command.data.content, revision: message.revision + 1, edited_at: stamp });
                    emit("message.updated", { message_id: message.id, message }); result = { message };
                } else if (command.type === "message.delete") {
                    const message = rows.find((row) => row.id === command.message_id)!;
                    Object.assign(message, { content: "", deleted_at: stamp, revision: message.revision + 1, attachments: [], entities: [], mentions: [], reactions: [] });
                    emit("message.deleted", { message_id: message.id, message }); result = { message };
                } else if (command.type === "reaction.set") {
                    const message = rows.find((row) => row.id === command.message_id)!;
                    message.reactions = command.data.active ? [{ reaction: command.data.reaction, user_ids: [userId] }] : [];
                    emit("reaction.updated", { message_id: message.id, message }); result = { message };
                } else if (command.type === "read.set") {
                    const boundary = rows.find((row) => row.id === command.data.message_id)?.seq ?? 0;
                    reads.set(userId, Math.max(reads.get(userId) ?? 0, boundary));
                    emit("read.updated", { user_id: userId, last_read_seq: reads.get(userId) }); result = { last_read_seq: reads.get(userId), unread_count: 0 };
                } else if (command.type.startsWith("typing.")) {
                    for (const peer of sockets.keys()) peer.send(JSON.stringify({ type: "typing.updated", project_id: 1, data: { user_ids: command.type === "typing.started" ? [userId] : [] } }));
                }
                socket.send(JSON.stringify({ type: "ack", request_id: command.request_id, data: result }));
            });
        });
    };
    return { attach, add, addAris, emit, rows, entity, sockets, control, tasks, taskEntity, taskSource, documentSource, fileSource, attachmentSource, document, projectFile, attachment };
}

const PENDING_CONFIRM = "0f1e2d3c-0000-4000-8000-000000000001";

async function installPersonalHistory(page: Page) {
    const stamp = "2026-09-21T10:00:00Z";
    const state = { reindexCalls: 0, requests: [] as string[] };
    await page.route(/\/api\/v1\/(?:projects\/1\/(?:aris\/conversations|knowledge\/)|aris\/pending\/|aris\/conversations)/, async (route) => {
        const request = route.request();
        const path = new URL(request.url()).pathname;
        const headers = { "access-control-allow-origin": "http://127.0.0.1:4179", "access-control-allow-credentials": "true", "access-control-allow-headers": "content-type", "access-control-allow-methods": "GET, POST, OPTIONS" };
        const reply = (json: unknown) => route.fulfill({ json, headers });
        if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
        state.requests.push(path);
        if (path === "/api/v1/aris/conversations") return reply({ items: [], next_offset: null });
        if (path.endsWith("/aris/conversations")) return reply({ items: [{ id: 17, project_id: 1, title: "План проекта", created_at: stamp, updated_at: stamp }], next_offset: null });
        if (path.endsWith("/aris/conversations/17/messages")) return reply({ items: [{ id: 170, conversation_id: 17, request_id: "history", role: "assistant", content: "Сохранённый план проверки проекта", sources: [], files: [], actions: [], status: "completed", error: null, created_at: stamp }], next_before_id: null });
        if (path.endsWith("/knowledge/reindex")) { state.reindexCalls += 1; return reply({ queued: true }); }
        if (path.endsWith("/knowledge/status")) return reply({ enabled: true, ready: true, points_count: 23, pending_jobs: 0, processing_jobs: 0, failed_jobs: 1, last_error: null, obsolete_points: 0, coverage: [{ entity_type: "document", total: 2, indexed: 1, missing: 1, stale: 0 }], file_issues: [{ source_id: "attachment:9", title: "План.pdf", status: "failed", detail: "Текст файла недоступен" }] });
        if (path.endsWith(`/aris/pending/${PENDING_CONFIRM}`)) return reply({ id: PENDING_CONFIRM, tool_name: "telos_delete_task", title: "Удалить задачу", status: "pending", arguments: { task_id: 42 }, preview: {}, result: {}, project: { id: 1, key: "DEMO", name: "Портал заказов" }, created_at: stamp, expires_at: "2026-10-01T10:00:00Z" });
        return reply([]);
    });
    return state;
}

async function openChat(page: Page) {
    await page.bringToFront();
    await page.goto("/projects/DEMO/chat");
    await expect(page.getByText("Подключено", { exact: true })).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole("combobox", { name: "Сообщение проекту" })).toBeVisible();
}

test("одна вкладка чата и обращение к Арису по начальному имени", async ({ page }) => {
    const server = chatServer(); await server.attach(page); await openChat(page);
    const sections = page.getByRole("navigation", { name: "Разделы проекта" });
    await expect(sections.getByRole("link").nth(0)).toHaveAccessibleName("Обзор");
    await expect(sections.getByRole("link").nth(1)).toHaveAccessibleName("Чат");
    await expect(sections.getByRole("link", { name: "Пульс", exact: true })).toHaveCount(0);
    await expect(sections.getByRole("link", { name: "Чат", exact: true })).toHaveCount(1);
    await expect(sections.getByRole("link", { name: "Агент", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Открыть чат агента" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Открыть чат команды" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Открыть чаты", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Спросить Ариса", exact: true })).toHaveCount(0);
    await expect(page.getByRole("dialog", { name: "Чаты", exact: true })).toHaveCount(0);
    await expect(page.getByRole("combobox", { name: "Выбор чата", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Личные диалоги и знания", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Проектные диалоги и знания", exact: true })).toHaveCount(0);
    const input = page.getByRole("combobox", { name: "Сообщение проекту" });
    for (const text of ["Тим, что по задачам?", "Вера, какие сроки?", "Аристотель", "ARISTOTLE", "Арис2", "Арис_бот", "ARIS_bot", "коллеги, Арис сказал"]) {
        await input.fill(text);
        await expect(page.getByText("Начните сообщение с «Арис, …», чтобы обратиться к проектному менеджеру", { exact: true })).toBeVisible();
        await expect(page.getByText("Арис — проектный менеджер — ответит здесь, в чате проекта", { exact: true })).toHaveCount(0);
    }
    await input.fill("  aRiS: какие сроки ближайшие?");
    await expect(page.getByText("Арис — проектный менеджер — ответит здесь, в чате проекта", { exact: true })).toBeVisible();
    await input.fill("Арис, какие сроки ближайшие?");
    await expect(page.getByText("Арис — проектный менеджер — ответит здесь, в чате проекта", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Отправить", exact: true }).click();
    await expect(page.getByRole("article").filter({ hasText: "Арис, какие сроки ближайшие?" })).toBeVisible();
    expect(server.control.commands.find((command) => command.type === "message.send")?.data.content).toBe("Арис, какие сроки ближайшие?");
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByRole("button", { name: "Открыть чаты", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Спросить Ариса", exact: true })).toHaveCount(0);
    await input.evaluate((element) => { element.style.height = "180px"; });
    const resizedEditor = (await input.boundingBox())!;
    expect(resizedEditor.height).toBeGreaterThanOrEqual(44);
    expect(resizedEditor.height, "Редактор ограничивает высоту и оставляет место истории и отправке").toBeLessThanOrEqual(180);
    await input.fill("Проверка после изменения высоты редактора");
    await expect(page.getByRole("button", { name: "Отправить", exact: true })).toBeInViewport();
    await page.getByRole("button", { name: "Отправить", exact: true }).click();
    await expect(page.getByRole("article").filter({ hasText: "Проверка после изменения высоты редактора" })).toBeVisible();
    expect(server.control.commands.filter((command) => command.type === "message.send").at(-1)?.data.content).toBe("Проверка после изменения высоты редактора");
});

test("чат и контекст делят рабочую область на 1920 px, на 320 px контекст открывается отдельно", async ({ page }, info) => {
    watchBrowserErrors(page);
    await page.setViewportSize({ width: 1920, height: 1080 });
    const server = chatServer();
    server.add("Сообщение на широком экране", 2, false, { entities: [server.entity] });
    await server.attach(page); await openChat(page);
    const sections = page.getByRole("navigation", { name: "Разделы проекта" });
    const input = page.getByRole("combobox", { name: "Сообщение проекту" });
    const assertActiveTabFullyVisible = async () => {
        await expect.poll(async () => {
            const tab = (await sections.getByRole("link", { name: "Чат", exact: true }).boundingBox())!;
            const nav = (await sections.boundingBox())!;
            return Math.max(nav.x - tab.x, tab.x + tab.width - nav.x - nav.width, 0);
        }, { message: "Активная вкладка целиком видна внутри полосы разделов" }).toBeLessThanOrEqual(1);
    };
    const assertProjectChatOnly = async () => {
        await expect(page.getByRole("combobox", { name: "Выбор чата", exact: true })).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Личные диалоги и знания", exact: true })).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Проектные диалоги и знания", exact: true })).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Открыть чаты", exact: true })).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Спросить Ариса", exact: true })).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Открыть чат команды", exact: true })).toHaveCount(0);
        await expect(page.getByRole("dialog", { name: "Чаты", exact: true })).toHaveCount(0);
    };
    await assertProjectChatOnly();
    await expect(page.locator("body")).toHaveCSS("font-size", "14px");
    await expect(sections.getByRole("link", { name: "Чат", exact: true })).toHaveCSS("font-size", "15px");
    await expect(page.getByText("Сообщение на широком экране", { exact: true })).toBeVisible();
    const workspace = (await page.locator("#main-content").boundingBox())!;
    const tabs = (await sections.boundingBox())!;
    const firstTab = (await sections.getByRole("link").first().boundingBox())!;
    const lastTab = (await sections.getByRole("link").last().boundingBox())!;
    const messages = (await page.getByLabel("История сообщений проекта", { exact: true }).locator(":scope > div").first().boundingBox())!;
    const history = (await page.getByLabel("История сообщений проекта", { exact: true }).boundingBox())!;
    const editor = (await input.boundingBox())!;
    const context = page.getByRole("complementary", { name: "Контекст чата", exact: true });
    await expect(context.getByText("Объекты из переписки", { exact: true })).toBeVisible();
    const sidebar = (await context.boundingBox())!;
    const panelGaps = await context.evaluate(element => {
        const panel = element.getBoundingClientRect();
        const parent = element.parentElement!.getBoundingClientRect();
        return { top: panel.top - parent.top, right: parent.right - panel.right, bottom: parent.bottom - panel.bottom };
    });
    const composer = (await page.locator(".project-chat-workspace .chat-composer").boundingBox())!;
    expect(tabs.width, "Навигация проекта использует ширину рабочей области").toBeGreaterThan(workspace.width - 64);
    expect(Math.abs(firstTab.x - tabs.x)).toBeLessThanOrEqual(2);
    expect(Math.abs(lastTab.x + lastTab.width - tabs.x - tabs.width), "Вкладки распределены по всей ширине навигации").toBeLessThanOrEqual(2);
    expect(sidebar.width, "Панель контекста сохраняет рабочую ширину").toBeGreaterThanOrEqual(360);
    expect(sidebar.width).toBeLessThanOrEqual(460);
    for (const gap of Object.values(panelGaps)) {
        expect(gap, "Контекст отступает от границ рабочей области").toBeGreaterThanOrEqual(12);
        expect(gap).toBeLessThanOrEqual(24);
    }
    expect(history.x + history.width, "Контекст не перекрывает историю").toBeLessThanOrEqual(sidebar.x + 1);
    const leftOffset = messages.x - history.x;
    expect(leftOffset).toBeGreaterThanOrEqual(0);
    expect(leftOffset, "Переписка сдвинута влево от прежнего центрирования").toBeLessThan((history.width - messages.width) / 2 - 4);
    expect(Math.abs(composer.x - messages.x), "История и редактор выровнены").toBeLessThanOrEqual(2);
    expect(messages.width, "Строки переписки ограничены 900 px").toBeLessThanOrEqual(900);
    expect(messages.width).toBeGreaterThan(800);
    expect(editor.width, "Редактор следует ширине переписки").toBeLessThanOrEqual(900);
    expect(editor.width).toBeGreaterThan(700);
    await expect(page.getByLabel("История сообщений проекта", { exact: true }).locator(":scope > div").first(), "Ограничение ширины задано CSS").toHaveCSS("max-width", "900px");
    await expectBrandPaint(context);
    await expectBrandPaint(context.locator(":scope > header"), "::after");
    await page.screenshot({ path: info.outputPath("project-chat-wide.png"), animations: "disabled" });
    await page.setViewportSize({ width: 320, height: 844 });
    await assertActiveTabFullyVisible();
    await page.reload();
    await expect(page.getByText("Подключено", { exact: true })).toBeVisible();
    await assertProjectChatOnly();
    await expect(context).toHaveCount(0);
    await expect(page.getByRole("dialog", { name: "Контекст чата", exact: true })).toHaveCount(0);
    await expect(sections.getByRole("link", { name: "Чат", exact: true })).toBeInViewport();
    await assertActiveTabFullyVisible();
    await expect(input).toBeInViewport();
    await page.getByRole("button", { name: "Показать контекст", exact: true }).click();
    const drawer = page.getByRole("dialog", { name: "Контекст чата", exact: true });
    await expect(drawer).toBeVisible();
    await expect(drawer.getByText("Объекты из переписки", { exact: true })).toBeVisible();
    await expectBrandPaint(drawer.locator(".brand-ai-header"), "::after");
    expect((await drawer.boundingBox())!.width).toBeLessThanOrEqual(320);
    await page.screenshot({ path: info.outputPath("project-chat-context-320.png"), animations: "disabled" });
    await drawer.getByRole("button", { name: "Закрыть контекст", exact: true }).click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), "Узкая страница не прокручивается по горизонтали").toBe(true);
    await input.fill("Проверка чата на экране 320 px");
    await expect(page.getByRole("button", { name: "Отправить", exact: true })).toBeInViewport();
    await page.getByRole("button", { name: "Отправить", exact: true }).click();
    await expect(page.getByRole("article").filter({ hasText: "Проверка чата на экране 320 px" })).toBeVisible();
    expect(server.control.commands.filter((command) => command.type === "message.send").at(-1)?.data.content).toBe("Проверка чата на экране 320 px");
    await assertActiveTabFullyVisible();
    await page.screenshot({ path: info.outputPath("project-chat-320.png"), animations: "disabled" });
});

test("контекст задачи позволяет писать, переключать карточки и сохраняет закреплённый выбор", async ({ page }, info) => {
    watchBrowserErrors(page);
    await page.setViewportSize({ width: 1920, height: 1080 });
    const server = chatServer();
    server.add("Разбираем задачи заказа", 2, false, { entities: [server.taskEntity(42), server.taskEntity(43), server.entity] });
    await server.attach(page); await openChat(page);
    const panel = page.getByRole("complementary", { name: "Контекст чата", exact: true });
    const history = page.getByLabel("История сообщений проекта", { exact: true });
    const input = page.getByRole("combobox", { name: "Сообщение проекту" });
    const firstCard = history.getByRole("button", { name: `Задача: ${server.tasks.get(42)!.title}`, exact: true });
    const secondCard = history.getByRole("button", { name: `Задача: ${server.tasks.get(43)!.title}`, exact: true });
    await expect(panel.getByText("Объекты из переписки", { exact: true })).toBeVisible();
    await expect(panel.getByText(server.tasks.get(42)!.description_md!, { exact: true })).toHaveCount(0);
    await input.fill("Черновик обсуждения заказа");
    await firstCard.click();
    await expectTaskDialogThenClose(page, server.tasks.get(42)!, info.outputPath("project-chat-human-task-modal.png"));
    await expect(panel.getByText(server.tasks.get(42)!.description_md!, { exact: true })).toBeVisible();
    await expect(firstCard).toHaveAttribute("aria-pressed", "true");
    await expect(input).toHaveValue("Черновик обсуждения заказа");
    await expect(page).toHaveURL(/\/projects\/DEMO\/chat$/);
    await input.fill("Черновик обсуждения заказа — продолжаю писать");
    await expect(input).toBeEditable();
    await secondCard.click();
    await expectTaskDialogThenClose(page, server.tasks.get(43)!);
    await expect(panel.getByText(server.tasks.get(43)!.description_md!, { exact: true })).toBeVisible();
    await expect(panel.getByText(server.tasks.get(42)!.description_md!, { exact: true })).toHaveCount(0);
    await panel.getByRole("button", { name: "Закрепить контекст", exact: true }).click();
    server.addAris("Проверим первую задачу перед выпуском", "completed", [server.taskSource(42)]);
    await expect(history.getByText("Проверим первую задачу перед выпуском", { exact: true })).toBeVisible();
    await expect(panel.getByText(server.tasks.get(43)!.description_md!, { exact: true })).toBeVisible();
    await expect(panel.getByRole("button", { name: "Открепить контекст", exact: true })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("dialog", { name: /^Задача/ })).toHaveCount(0);
    const recent = panel.getByRole("region", { name: "Объекты из переписки", exact: true });
    await recent.getByRole("button", { name: `Задача ${server.taskSource(42).title}`, exact: true }).click();
    await expect(panel.getByText(server.tasks.get(42)!.description_md!, { exact: true })).toBeVisible();
    await expect(page.getByRole("dialog", { name: /^Задача/ })).toHaveCount(0);
    await recent.getByRole("button", { name: `Задача ${server.tasks.get(43)!.title}`, exact: true }).click();
    await expect(panel.getByText(server.tasks.get(43)!.description_md!, { exact: true })).toBeVisible();
    await expect(page.getByRole("dialog", { name: /^Задача/ })).toHaveCount(0);
    await firstCard.click();
    await expectTaskDialogThenClose(page, server.tasks.get(42)!);
    await expect(panel.getByText(server.tasks.get(42)!.description_md!, { exact: true })).toBeVisible();
    await expect(panel.getByRole("button", { name: "Открепить контекст", exact: true })).toBeVisible();
    server.tasks.get(42)!.description_md = "Проверить обязательные поля, сохранение и подтверждение заказа.";
    server.emit("entity.updated", { entity_type: "TASK", entity_id: 42 });
    await expect(panel.getByText(server.tasks.get(42)!.description_md!, { exact: true })).toBeVisible();
    await panel.getByRole("button", { name: "Закрыть контекст", exact: true }).click();
    await expect(panel).toHaveCount(0);
    await expect(input).toHaveValue("Черновик обсуждения заказа — продолжаю писать");
    await page.getByRole("button", { name: "Показать контекст", exact: true }).click();
    await expect(panel.getByText(server.tasks.get(42)!.description_md!, { exact: true })).toBeVisible();
    await expect(input).toHaveValue("Черновик обсуждения заказа — продолжаю писать");
    await page.screenshot({ path: info.outputPath("project-chat-task-context.png"), animations: "disabled" });
    await panel.getByRole("button", { name: "Открыть карточку задачи", exact: true }).click();
    await expectTaskDialogThenClose(page, server.tasks.get(42)!);
    await expect(panel.getByText(server.tasks.get(42)!.description_md!, { exact: true })).toBeVisible();
    await expect(input).toHaveValue("Черновик обсуждения заказа — продолжаю писать");
});

test("источники Ариса открываются рядом, авто-выбор ждёт готовый однозначный ответ", async ({ page }, info) => {
    watchBrowserErrors(page);
    const server = chatServer();
    server.addAris("Материалы для проверки", "completed", [{ ...server.taskSource(42), task_id: null }, server.documentSource, server.fileSource], false);
    await server.attach(page); await openChat(page);
    const panel = page.getByRole("complementary", { name: "Контекст чата", exact: true });
    const history = page.getByLabel("История сообщений проекта", { exact: true });
    const input = page.getByRole("combobox", { name: "Сообщение проекту" });
    await expect(panel.getByText("Задача или источник рядом с чатом", { exact: true })).toBeVisible();
    await input.fill("Продолжаю обсуждать источники");
    for (const [label, body] of [
        [`Задача: ${server.taskSource(42).title}`, server.tasks.get(42)!.description_md!],
        [`Документ: ${server.documentSource.title}`, server.document.content_md],
        [`Документ: ${server.fileSource.title}`, server.projectFile.rawText],
    ]) {
        const sourceCard = history.getByRole("button", { name: label, exact: true });
        const restingPaint = await sourceCard.evaluate(element => getComputedStyle(element).backgroundImage);
        await expectBrandPaint(sourceCard);
        await expectBrandPaint(sourceCard, "::before");
        await focusCardWithKeyboard(page, sourceCard);
        await sourceCard.press("Enter");
        if (label === `Задача: ${server.taskSource(42).title}`) {
            await expectTaskDialogThenClose(page, server.tasks.get(42)!, info.outputPath("project-chat-agent-task-modal.png"));
        } else {
            await expect(page.getByRole("dialog", { name: /^Задача/ })).toHaveCount(0);
        }
        await expect(sourceCard).toHaveAttribute("aria-pressed", "true");
        await expect.poll(() => sourceCard.evaluate(element => getComputedStyle(element).backgroundImage), { message: "Выбор и клавиатурный фокус усиливают границу карточки" }).not.toBe(restingPaint);
        await expectBrandPaint(sourceCard);
        await expect(panel.getByText(body, { exact: true })).toBeVisible();
        if (label === `Документ: ${server.documentSource.title}`) {
            server.document.content_md = "Обновлённые критерии готовности интерфейса согласованы.";
            server.emit("entity.updated", { entity_type: "DOCUMENT", document_ref: server.document.ref });
            await expect(panel.getByText(server.document.content_md, { exact: true })).toBeVisible();
            server.document.content_md = "Повторно выбранный документ содержит свежие критерии.";
            await history.getByRole("button", { name: label, exact: true }).click();
            await expect(panel.getByText(server.document.content_md, { exact: true })).toBeVisible();
            server.document.content_md = "Новый ответ Ариса использует последнюю версию документа.";
            server.addAris("Свежая версия документа", "completed", [server.documentSource]);
            await expect(panel.getByText(server.document.content_md, { exact: true })).toBeVisible();
        }
        await expect(page).toHaveURL(/\/projects\/DEMO\/chat$/);
        await expect(page.getByRole("dialog", { name: /^Задача/ })).toHaveCount(0);
        await expect(input).toHaveValue("Продолжаю обсуждать источники");
        await expect(input).toBeEditable();
    }
    const pending = server.addAris("", "processing", [server.taskSource(43)]);
    await expect(history.getByText("Арис думает…", { exact: true })).toBeVisible();
    await expect(panel.getByText(server.projectFile.rawText, { exact: true })).toBeVisible();
    await expect(page.getByRole("dialog", { name: /^Задача/ })).toHaveCount(0);
    Object.assign(pending, { revision: 2, content: "Мобильная задача готова к обсуждению", agent: { ...pending.agent!, status: "completed" } });
    server.emit("message.updated", { message_id: pending.id, message: pending });
    await expect(panel.getByText(server.tasks.get(43)!.description_md!, { exact: true })).toBeVisible();
    await expect(page.getByRole("dialog", { name: /^Задача/ })).toHaveCount(0);
    server.addAris("Оба материала пригодятся", "completed", [server.documentSource, server.fileSource]);
    await expect(history.getByText("Оба материала пригодятся", { exact: true })).toBeVisible();
    await expect(panel.getByText(server.tasks.get(43)!.description_md!, { exact: true })).toBeVisible();
    await expect(page.getByRole("dialog", { name: /^Задача/ })).toHaveCount(0);
    await expect(input).toHaveValue("Продолжаю обсуждать источники");
});

for (const width of [1440, 390]) {
    test(`основной документ задачи открывает проверенный фрагмент из нескольких источников, ${width}px`, async ({ page }, info) => {
        watchBrowserErrors(page);
        await page.setViewportSize({ width, height: 900 });
        const server = chatServer();
        server.addAris("Условия приёмки указаны в документе задачи.", "completed", [server.fileSource, { ...server.attachmentSource, is_primary: true }], false);
        await server.attach(page); await openChat(page);
        if (width < 1280) await page.getByRole("button", { name: "Показать контекст", exact: true }).click();
        const panel = width < 1280 ? page.getByRole("dialog", { name: "Контекст чата", exact: true }) : page.getByRole("complementary", { name: "Контекст чата", exact: true });
        await expect(panel.getByRole("heading", { name: server.attachment.title, exact: true })).toBeVisible();
        await expect(panel.locator("mark")).toHaveText(server.attachmentSource.reference!.quote);
        await expect(panel.locator("mark")).toBeInViewport();
        await expect(page.getByRole("dialog", { name: /^Задача/ })).toHaveCount(0);
        await expect(page).toHaveURL(/\/projects\/DEMO\/chat$/);
        await page.screenshot({ path: info.outputPath(`document-fragment-${width}.png`), animations: "disabled" });
        server.attachment.rawText = "Новая версия документа. Условия изменены.";
        server.emit("entity.updated", { entity_type: "DOCUMENT", document_ref: server.attachment.ref });
        await expect(panel.getByText(/Документ изменился или фрагмент недоступен/)).toBeVisible();
        await expect(panel.locator("mark")).toHaveCount(0);
        await expect(panel.getByLabel("Извлечённый текст", { exact: true })).toHaveText(server.attachment.rawText);
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    });
}

test("на телефоне карточка открывает задачу, контекст сохраняется без лишнего Drawer", async ({ page }, info) => {
    watchBrowserErrors(page);
    await page.setViewportSize({ width: 390, height: 844 });
    const server = chatServer();
    server.add("Обсуждаем задачу на телефоне", 2, false, { entities: [server.taskEntity(42), server.entity] });
    await server.attach(page); await openChat(page);
    const history = page.getByLabel("История сообщений проекта", { exact: true });
    const card = history.getByRole("button", { name: `Задача: ${server.tasks.get(42)!.title}`, exact: true });
    const input = page.getByRole("combobox", { name: "Сообщение проекту" });
    const drawer = page.getByRole("dialog", { name: "Контекст чата", exact: true });
    await expect(drawer).toHaveCount(0);
    await expect(page.getByRole("complementary", { name: "Контекст чата", exact: true })).toHaveCount(0);
    await input.fill("Мобильный черновик сохранён");
    await card.click();
    await expect(drawer).toHaveCount(0);
    await expectTaskDialogThenClose(page, server.tasks.get(42)!, info.outputPath("project-chat-mobile-task-modal.png"));
    await expect(drawer).toHaveCount(0);
    await expect(card).toBeFocused();
    await expect(input).toHaveValue("Мобильный черновик сохранён");
    await expect(input).toBeEditable();
    const contextToggle = page.getByRole("button", { name: "Показать контекст", exact: true });
    await contextToggle.click();
    await expect(drawer).toBeVisible();
    await expectBrandPaint(drawer.locator(".brand-ai-header"), "::after");
    await expect(drawer.getByText(server.tasks.get(42)!.description_md!, { exact: true })).toBeVisible();
    await expect(page.getByRole("dialog", { name: /^Задача/ })).toHaveCount(0);
    expect((await drawer.boundingBox())!.width).toBeLessThanOrEqual(390);
    await page.screenshot({ path: info.outputPath("project-chat-mobile-context.png"), animations: "disabled" });
    await page.keyboard.press("Escape");
    await expect(drawer).toHaveCount(0);
    await expect(contextToggle).toBeFocused();
    await expect(input).toHaveValue("Мобильный черновик сохранён");
    await page.getByRole("button", { name: "Показать контекст", exact: true }).click();
    await expect(drawer.getByText(server.tasks.get(42)!.description_md!, { exact: true })).toBeVisible();
    await drawer.getByRole("button", { name: "Закрыть контекст", exact: true }).click();
    server.addAris("Проверим мобильную задачу", "completed", [server.taskSource(43)]);
    await expect(history.getByText("Проверим мобильную задачу", { exact: true })).toBeVisible();
    await expect(drawer).toHaveCount(0);
    await expect(page.getByRole("dialog", { name: /^Задача/ })).toHaveCount(0);
    await page.getByRole("button", { name: "Показать контекст", exact: true }).click();
    await expect(drawer.getByText(server.tasks.get(43)!.description_md!, { exact: true })).toBeVisible();
    await expect(page.getByRole("dialog", { name: /^Задача/ })).toHaveCount(0);
    await drawer.getByRole("button", { name: "Закрыть контекст", exact: true }).click();
    await history.getByRole("button", { name: `Документ: ${server.document.title}`, exact: true }).click();
    await expect(drawer.getByText(server.document.content_md, { exact: true })).toBeVisible();
    await expect(page.getByRole("dialog", { name: /^Задача/ })).toHaveCount(0);
    await drawer.getByRole("button", { name: "Закрыть контекст", exact: true }).click();
    await expect(input).toHaveValue("Мобильный черновик сохранён");
    await expect(input).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("недоступный источник 403 или 404 очищает прежний preview и оставляет редактор", async ({ page }) => {
    watchBrowserErrors(page, true);
    const server = chatServer();
    server.addAris("Проверка доступа к источникам", "completed", [server.taskSource(42), server.documentSource, server.fileSource], false);
    await server.attach(page); await openChat(page);
    const panel = page.getByRole("complementary", { name: "Контекст чата", exact: true });
    const history = page.getByLabel("История сообщений проекта", { exact: true });
    await history.getByRole("button", { name: `Задача: ${server.taskSource(42).title}`, exact: true }).click();
    await expectTaskDialogThenClose(page, server.tasks.get(42)!);
    await expect(panel.getByText(server.tasks.get(42)!.description_md!, { exact: true })).toBeVisible();
    const input = page.getByRole("combobox", { name: "Сообщение проекту" });
    await input.fill("Обсуждение продолжается без источника");
    for (const [key, status, label] of [
        ["document:testing", 403, `Документ: ${server.documentSource.title}`],
        ["project_file:9", 404, `Документ: ${server.fileSource.title}`],
    ] as const) {
        server.control.sourceStatus.set(key, status);
        await history.getByRole("button", { name: label, exact: true }).click();
        await expect(panel.getByText(/недоступен|не найден|нет доступа/i).first()).toBeVisible();
        await expect(panel.getByText(server.tasks.get(42)!.description_md!, { exact: true })).toHaveCount(0);
        await expect(panel.getByText(server.document.content_md, { exact: true })).toHaveCount(0);
        await expect(panel.getByText(server.projectFile.rawText, { exact: true })).toHaveCount(0);
        await expect(input).toHaveValue("Обсуждение продолжается без источника");
        await expect(input).toBeEditable();
        await expect(page).toHaveURL(/\/projects\/DEMO\/chat$/);
        await expect(page.getByRole("dialog", { name: /^Задача/ })).toHaveCount(0);
    }
});

test("приватный проектный диалог сохраняет историю, подтверждение и контекст", async ({ page }) => {
    const server = chatServer(); await server.attach(page); await installPersonalHistory(page);
    await page.goto(`/projects/DEMO/conversations?conversation=17&confirm=${PENDING_CONFIRM}&task=42&tag=a&tag=b#context`);
    await expect(page).toHaveURL(/\/projects\/DEMO\/conversations\?/);
    const confirmation = page.getByRole("dialog", { name: "Подтверждение действия агента", exact: true });
    await expect(confirmation).toBeVisible();
    const redirected = new URL(page.url());
    expect(redirected.searchParams.has("view")).toBe(false);
    expect(redirected.searchParams.get("confirm")).toBe(PENDING_CONFIRM);
    expect(redirected.hash).toBe("#context");
    await confirmation.getByRole("button", { name: "Закрыть", exact: true }).last().click();
    await expect(page.getByText("Личные диалоги проекта · видно только вам", { exact: true })).toBeVisible();
    await expect(page.getByRole("combobox", { name: "Выбор чата", exact: true })).toHaveCount(0);
    await expect(page.getByText("Сохранённый план проверки проекта", { exact: true })).toBeVisible();
    await expect(page.getByRole("combobox", { name: "Диалог", exact: true })).toHaveValue("17");
    const current = new URL(page.url());
    expect(current.searchParams.get("conversation")).toBe("17");
    expect(current.searchParams.get("task")).toBe("42");
    expect(current.searchParams.getAll("tag")).toEqual(["a", "b"]);
    expect(current.searchParams.has("confirm")).toBe(false);
    expect(current.hash).toBe("#context");
    server.add("Сообщение команды во время личного разговора");
    await expect(page.getByRole("complementary", { name: "Новое сообщение проекта" })).toBeVisible();
    await page.getByRole("link", { name: "Общий чат", exact: true }).click();
    await expect(page.getByRole("combobox", { name: "Сообщение проекту" })).toBeVisible();
    await expect(page.getByText("Сохранённый план проверки проекта", { exact: true })).toHaveCount(0);
});

test("знания и переиндексация доступны из единого чата на телефоне", async ({ page }) => {
    const server = chatServer(); await server.attach(page); const state = await installPersonalHistory(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/projects/DEMO/knowledge?task=42#knowledge");
    await expect(page).toHaveURL(/\/projects\/DEMO\/knowledge\?task=42#knowledge$/);
    expect(new URL(page.url()).searchParams.get("task")).toBe("42");
    expect(new URL(page.url()).hash).toBe("#knowledge");
    const knowledge = page.getByRole("region", { name: "Знания проекта", exact: true });
    await expect(knowledge.getByText("Фрагментов", { exact: true })).toBeVisible();
    expect(state.requests.some(path => path.includes("/aris/conversations"))).toBe(false);
    await expect(knowledge.getByText("23", { exact: true })).toBeVisible();
    await expect(knowledge.getByText("План.pdf", { exact: true })).toBeVisible();
    await knowledge.getByRole("button", { name: "Переиндексировать", exact: true }).click();
    await expect.poll(() => state.reindexCalls).toBe(1);
    await page.goto("/chat");
    await page.getByRole("button", { name: "Проектные диалоги и знания", exact: true }).click();
    await expect(page.getByRole("link", { name: "DEMO · Знания", exact: true })).toHaveAttribute("href", "/projects/DEMO/knowledge");
    await page.getByRole("link", { name: "DEMO · Личные диалоги", exact: true }).click();
    await expect(page.getByText("Сохранённый план проверки проекта", { exact: true })).toBeVisible();
    expect(new URL(page.url()).pathname).toBe("/projects/DEMO/conversations");
    expect(new URL(page.url()).searchParams.get("conversation")).toBe("17");
    await expect(page.getByRole("combobox", { name: "Выбор чата", exact: true })).toHaveCount(0);
});

test("окно команды и вкладка разделяют сокет, историю, ответ и черновик редактирования", async ({ page }, info) => {
    const server = chatServer();
    server.add("Проверим плавающее окно", 2, false);
    await server.attach(page);
    await page.goto("/projects/DEMO/team");
    await page.getByRole("button", { name: "Открыть чаты", exact: true }).click();
    await expect(page.getByText("Подключено", { exact: true })).toBeVisible();
    const connections = server.control.connections;
    const original = page.getByRole("article").filter({ hasText: "Проверим плавающее окно" });
    await original.getByRole("button", { name: "Действия с сообщением" }).click();
    await page.getByRole("button", { name: "Ответить", exact: true }).click();
    await page.getByRole("combobox", { name: "Сообщение проекту" }).fill("Проверяю прямо из команды");
    await page.getByRole("button", { name: "Открыть чат во вкладке" }).click();
    await expect(page).toHaveURL(/\/chat$/);
    await expect(page.getByRole("button", { name: "Открыть чат команды", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Открыть чат агента", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Открыть чаты", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Спросить Ариса", exact: true })).toHaveCount(0);
    await expect(page.getByRole("dialog", { name: "Чаты", exact: true })).toHaveCount(0);
    await expect(page.getByRole("combobox", { name: "Сообщение проекту" })).toHaveValue("Проверяю прямо из команды");
    await page.getByRole("button", { name: "Отправить", exact: true }).click();
    expect(server.rows.at(-1)!.reply?.id).toBe(server.rows[0].id);
    const own = page.getByRole("article").filter({ hasText: "Проверяю прямо из команды" });
    await own.getByRole("button", { name: "Действия с сообщением" }).click();
    await page.getByRole("button", { name: "Изменить", exact: true }).click();
    await page.getByRole("combobox", { name: "Редактирование сообщения" }).fill("Проверено из двух представлений");
    await page.getByRole("link", { name: "Команда", exact: true }).click();
    await page.getByRole("button", { name: "Открыть чаты", exact: true }).click();
    await expect(page.getByRole("combobox", { name: "Редактирование сообщения" })).toHaveValue("Проверено из двух представлений");
    await page.getByRole("button", { name: "Сохранить", exact: true }).click();
    await expect(page.getByText("Проверено из двух представлений", { exact: true })).toBeVisible();
    await page.getByRole("navigation", { name: "Основная навигация" }).getByRole("link", { name: "Портфель", exact: true }).click();
    await expect(page).toHaveURL(/\/(?:\?view=projects)?$/);
    await expect(page.getByRole("dialog", { name: "Чаты", exact: true })).toBeVisible();
    await expect(page.getByText("Проверено из двух представлений", { exact: true })).toBeVisible();
    expect(server.control.connections).toBe(connections);
    await page.screenshot({ path: info.outputPath("floating-team-chat.png"), animations: "disabled" });
});

test("поиск и прочтение в окне команды не меняют основной экран, окно помещается на телефоне", async ({ page }, info) => {
    const server = chatServer();
    for (let i = 0; i < 65; i++) server.add(i === 0 ? "Давнее решение по приёмке" : `Сообщение ${i}`, 2, false);
    await server.attach(page);
    await page.goto("/projects/DEMO/team");
    await page.getByRole("button", { name: "Открыть чаты", exact: true }).click();
    await expect(page.getByText("Подключено", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Поиск в чате" }).click();
    await page.getByRole("searchbox", { name: "Поиск по сообщениям" }).fill("Давнее решение");
    await page.getByRole("complementary", { name: "Поиск по сообщениям" }).getByRole("button").filter({ hasText: "Давнее решение" }).click();
    await expect(page.getByRole("article").filter({ hasText: "Давнее решение по приёмке" })).toBeVisible();
    await expect(page).toHaveURL(/\/team$/);
    await page.getByRole("button", { name: "К последним сообщениям" }).click();
    await expect.poll(() => server.control.commands.some((command) => command.type === "read.set")).toBe(true);
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByRole("combobox", { name: "Сообщение проекту" })).toBeVisible();
    await page.screenshot({ path: info.outputPath("floating-team-mobile.png"), animations: "disabled" });
    const panel = await page.getByRole("dialog", { name: "Чаты", exact: true }).boundingBox();
    expect(panel!.x).toBeGreaterThanOrEqual(0);
    expect(panel!.x + panel!.width).toBeLessThanOrEqual(390);
    await page.getByRole("combobox", { name: "Сообщение проекту" }).press("Escape");
    await expect(page.getByRole("dialog", { name: "Чаты", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Открыть чаты", exact: true })).toBeFocused();
});

test("два участника получают сообщения, ответы и реакции; история сохраняется после reload", async ({ page, context }) => {
    const server = chatServer();
    const second = await context.newPage();
    await server.attach(page, 1); await server.attach(second, 2);
    await openChat(page); await openChat(second);
    await page.getByRole("combobox", { name: "Сообщение проекту" }).fill("Проверим окно создания заказа");
    await page.getByRole("button", { name: "Отправить", exact: true }).click();
    await expect(second.getByText("Проверим окно создания заказа", { exact: true })).toBeVisible();
    const original = second.getByRole("article", { name: "Сообщение Анна Иванова", exact: true });
    await original.getByRole("button", { name: "Действия с сообщением" }).click();
    await second.getByRole("button", { name: "Ответить", exact: true }).click();
    await second.getByRole("combobox", { name: "Сообщение проекту" }).fill("Возьму проверку на себя");
    await second.getByRole("button", { name: "Отправить", exact: true }).click();
    await expect(page.getByText("Возьму проверку на себя", { exact: true })).toBeVisible();
    await original.getByRole("button", { name: "Добавить реакцию" }).click();
    await second.getByRole("button", { name: "Реакция 👍", exact: true }).click();
    await expect(page.getByRole("button", { name: "👍: 1" })).toBeVisible();
    await page.reload();
    await expect(page.getByText("Возьму проверку на себя", { exact: true })).toBeVisible();
    expect(server.rows).toHaveLength(2);
});

test("упоминания, ссылки и файлы используют общий редактор", async ({ page }) => {
    const server = chatServer(); await server.attach(page); await openChat(page);
    await page.getByRole("combobox", { name: "Сообщение проекту" }).fill("@oleg");
    await page.getByRole("option", { name: /Олег Петров/ }).click();
    await page.getByRole("button", { name: "Добавить ссылку на объект" }).click();
    await page.getByRole("option", { name: /План тестирования/ }).click();
    await page.getByLabel("Файлы сообщения").setInputFiles({ name: "acceptance.txt", mimeType: "text/plain", buffer: Buffer.from("Критерии") });
    await expect(page.getByText("acceptance.txt", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Отправить", exact: true }).click();
    const row = page.getByRole("article");
    await expect(row.getByRole("button", { name: "Документ: План тестирования" })).toBeVisible();
    await expect(row.getByRole("link", { name: /acceptance.txt/ })).toBeVisible();
    expect(server.rows[0].mentions.map((user) => user.id)).toEqual([2]);
    server.entity.title = "План тестирования — согласован";
    server.emit("entity.updated", { entity_type: "DOCUMENT", document_ref: server.document.ref });
    await expect(row.getByRole("button", { name: "Документ: План тестирования — согласован" })).toBeVisible();
});

test("редактирование, цитирование, удаление и поиск с переходом к сообщению", async ({ page }) => {
    const server = chatServer(); server.add("Решение по интерфейсу", 1, false); await server.attach(page); await openChat(page);
    const own = page.getByRole("article").filter({ hasText: "Решение по интерфейсу" });
    await own.getByRole("button", { name: "Действия с сообщением" }).click();
    await page.getByRole("button", { name: "Изменить", exact: true }).click();
    await page.getByRole("combobox", { name: "Редактирование сообщения" }).fill("Согласованное решение по интерфейсу");
    await page.getByRole("button", { name: "Сохранить", exact: true }).click();
    await expect(page.getByText("изменено", { exact: true })).toBeVisible();
    await own.getByRole("button", { name: "Действия с сообщением" }).click();
    await page.getByRole("button", { name: "Цитировать", exact: true }).click();
    await expect(page.getByRole("combobox", { name: "Сообщение проекту" })).toHaveValue(/> Согласованное решение/);
    await page.getByRole("button", { name: "Отправить", exact: true }).click();
    await expect(page.locator("blockquote")).toHaveText("Согласованное решение по интерфейсу");
    await page.getByRole("button", { name: "Поиск в чате" }).click();
    await page.getByRole("searchbox", { name: "Поиск по сообщениям" }).fill("Согласованное");
    await expect(page.getByRole("complementary", { name: "Поиск по сообщениям" }).getByRole("button").filter({ hasText: "Согласованное" })).toHaveCount(2);
    await own.first().getByRole("button", { name: "Действия с сообщением" }).click();
    await page.getByRole("button", { name: "Удалить", exact: true }).click();
    await page.getByRole("button", { name: "Удалить сообщение", exact: true }).click();
    await expect(page.getByText("Сообщение удалено", { exact: true })).toBeVisible();
    await expect(page.getByRole("complementary", { name: "Поиск по сообщениям" }).getByRole("button").filter({ hasText: "Согласованное" })).toHaveCount(1);
});

test("счётчик и уведомление обновляются на другой вкладке проекта без polling истории", async ({ page }) => {
    const server = chatServer(); await server.attach(page); await openChat(page);
    const connections = server.control.connections;
    await page.getByRole("link", { name: "Команда", exact: true }).click();
    await expect(page).toHaveURL(/\/team$/);
    await expect(page.getByRole("heading", { name: "Команда", exact: true })).toBeVisible();
    server.add("Обсудим критерии приёмки");
    await expect(page.getByLabel("Непрочитанных сообщений: 1")).toBeVisible();
    await expect(page.getByRole("complementary", { name: "Новое сообщение проекта" })).toBeVisible();
    expect(server.control.connections).toBe(connections);
    const historyRequests = server.control.messageGets;
    await page.waitForTimeout(1600);
    expect(server.control.messageGets).toBe(historyRequests);
    await page.getByRole("navigation", { name: "Разделы проекта" }).getByRole("link", { name: /^Чат/ }).click();
    await expect(page.getByText("Обсудим критерии приёмки", { exact: true }).first()).toBeVisible();
});

test("переносы задач появляются в общем чате без повторов и скрываются по одному", async ({ page }) => {
    const server = chatServer(); await server.attach(page); await openChat(page);
    const outgoing = server.emit("task.moved", { task_id: 42, from_key: "DEMO-42", user_id: 2, direction: "out", target_kind: "PROJECT", target_name: null, to_key: null });
    const notices = page.getByRole("list", { name: "Переносы задач" });
    await expect(notices.getByText("Задача DEMO-42 перенесена в другой проект — Олег Петров")).toBeVisible();
    for (const socket of server.sockets.keys()) socket.send(JSON.stringify(outgoing));
    server.emit("task.moved", { task_id: 43, from_key: "PERSONAL-9", user_id: 1, direction: "in", target_kind: "PROJECT", target_name: "Портал заказов", to_key: "DEMO-43" });
    await expect(notices.getByRole("listitem")).toHaveCount(2);
    await expect(notices.getByText("Задача PERSONAL-9 перенесена в этот проект: теперь DEMO-43 — Анна Иванова")).toBeVisible();
    await notices.getByRole("button", { name: "Скрыть уведомление о переносе" }).first().click();
    await expect(notices.getByRole("listitem")).toHaveCount(1);
    await expect(notices.getByText(/DEMO-42/)).toHaveCount(0);
});

test("уведомление, ответ и поиск называют Ариса при пустом author", async ({ page }) => {
    const server = chatServer(); await server.attach(page); await openChat(page);
    await page.getByRole("link", { name: "Команда", exact: true }).click();
    await expect(page).toHaveURL(/\/team$/);
    server.addAris("", "processing");
    await expect(page.getByRole("complementary", { name: "Новое сообщение проекта" })).toHaveCount(0);
    server.addAris("План проверки готов");
    const notification = page.getByRole("complementary", { name: "Новое сообщение проекта" });
    await expect(notification.getByText("Арис: План проверки готов")).toBeVisible();
    await notification.getByRole("button").first().click();
    const answer = page.getByRole("article", { name: "Сообщение Ариса", exact: true }).filter({ hasText: "План проверки готов" });
    const avatar = answer.locator('img');
    await expect(avatar).toHaveAttribute("src", /\/brand\/aris-avatar-/);
    await expect.poll(() => avatar.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBeGreaterThan(0);
    await answer.getByRole("button", { name: "Действия с сообщением" }).click();
    await page.getByRole("button", { name: "Ответить", exact: true }).click();
    await expect(page.getByText("Ответ Арису: План проверки готов", { exact: true })).toBeVisible();
    await page.getByRole("combobox", { name: "Сообщение проекту" }).fill("Спасибо за план");
    await page.getByRole("button", { name: "Отправить", exact: true }).click();
    const reply = page.getByRole("article", { name: "Сообщение Анна Иванова", exact: true });
    await expect(reply.getByRole("button", { name: "Перейти к исходному сообщению" }).getByText("Арис", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Поиск в чате" }).click();
    const search = page.getByRole("complementary", { name: "Поиск по сообщениям" });
    await search.getByRole("searchbox", { name: "Поиск по сообщениям" }).fill("План проверки готов");
    await expect(search.getByRole("button").filter({ hasText: "План проверки готов" }).getByText("Арис", { exact: true })).toBeVisible();
});

test("потерянный ack и reload сохраняют UUID и не дублируют сообщение", async ({ page }) => {
    const server = chatServer(); await server.attach(page); await openChat(page);
    server.control.dropNextAck = true;
    await page.getByRole("combobox", { name: "Сообщение проекту" }).fill("Сообщение с потерянным подтверждением");
    await page.getByRole("button", { name: "Отправить", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("Соединение прервано");
    await page.reload();
    await expect(page.getByText("Подключено", { exact: true })).toBeVisible();
    await expect(page.getByRole("combobox", { name: "Сообщение проекту" })).toHaveValue("Сообщение с потерянным подтверждением");
    await page.getByRole("button", { name: "Отправить", exact: true }).click();
    await expect(page.getByRole("combobox", { name: "Сообщение проекту" })).toHaveValue("");
    expect(server.rows).toHaveLength(1);
});

test("подгрузка ранней истории сохраняет прокрутку и показывает контекст поиска", async ({ page }) => {
    const server = chatServer();
    for (let i = 1; i <= 85; i += 1) server.add(`Обсуждение номер ${i}`, 2, false);
    await server.attach(page); await openChat(page);
    await expect(page.getByText("Обсуждение номер 85", { exact: true })).toBeVisible();
    const viewport = page.getByLabel("История сообщений проекта");
    await viewport.evaluate((element) => { element.scrollTop = 0; });
    await expect(page.getByRole("article")).toHaveCount(85);
    await expect(page.getByText("Обсуждение номер 36", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Поиск в чате" }).click();
    await page.getByRole("searchbox", { name: "Поиск по сообщениям" }).fill("номер 8");
    await page.getByRole("complementary", { name: "Поиск по сообщениям" }).getByRole("button").filter({ hasText: "Обсуждение номер 8" }).last().click();
    await expect(page.getByText("Обсуждение номер 8", { exact: true }).first()).toBeVisible();
});

test("спокойная лента и редактор доступны с клавиатуры", async ({ page }, testInfo) => {
    const server = chatServer(); server.add("Команда, окно создания заказа готово к проверке.", 2, false); server.add("**Критерии приёмки**\n> Обязательные поля подсвечиваются, данные сохраняются.\nПроверим также клавиатурную навигацию.", 1, false);
    await server.attach(page); await openChat(page);
    const input = page.getByRole("combobox", { name: "Сообщение проекту" });
    await input.focus(); await input.fill("@ol"); await input.press("ArrowDown"); await input.press("Enter");
    await expect(input).toHaveValue("@oleg ");
    await input.press("Shift+Enter"); await input.type("Проверим вместе");
    await input.press("Enter");
    await expect(page.getByText("Проверим вместе", { exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("project-chat.png"), fullPage: true });
});

test("цитируется выделенный фрагмент, typing и отзыв доступа доходят в открытый чат", async ({ page }) => {
    const server = chatServer(); server.add("Согласованная часть. Остальной текст.", 2, false);
    await server.attach(page); await openChat(page);
    const text = page.getByText("Согласованная часть. Остальной текст.", { exact: true });
    await text.evaluate((element) => {
        const range = document.createRange();
        range.setStart(element.firstChild!, 0); range.setEnd(element.firstChild!, "Согласованная часть.".length);
        window.getSelection()!.removeAllRanges(); window.getSelection()!.addRange(range);
    });
    await page.getByRole("article").getByRole("button", { name: "Действия с сообщением" }).click();
    await page.getByRole("button", { name: "Цитировать", exact: true }).click();
    await expect(page.getByRole("combobox", { name: "Сообщение проекту" })).toHaveValue("> Согласованная часть.\n\n");
    for (const socket of server.sockets.keys()) socket.send(JSON.stringify({ type: "typing.updated", project_id: 1, data: { user_ids: [2] } }));
    await expect(page.getByText(/Олег Петров.*печатает/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Участники чата" })).toContainText("2 в сети");
    server.control.blocked = true;
    for (const socket of server.sockets.keys()) await socket.close({ code: 4403 });
    await expect(page.getByRole("combobox", { name: "Сообщение проекту" })).toHaveCount(0);
    await expect(page.getByText("Чат недоступен", { exact: true })).toBeVisible();
});

test("на узком экране редактор, карточки и поиск остаются доступны", async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const server = chatServer(); server.add("Проверим мобильный интерфейс", 2, false);
    await server.attach(page); await openChat(page);
    await page.getByRole("button", { name: "Добавить ссылку на объект" }).click();
    await page.getByRole("option", { name: /План тестирования/ }).click();
    await page.getByRole("button", { name: "Отправить", exact: true }).click();
    await expect(page.getByRole("button", { name: "Документ: План тестирования" })).toBeVisible();
    await page.getByRole("button", { name: "Поиск в чате" }).click();
    await page.getByRole("searchbox", { name: "Поиск по сообщениям" }).fill("мобильный");
    await expect(page.getByRole("complementary", { name: "Поиск по сообщениям" })).toContainText("Проверим мобильный интерфейс");
    await page.getByRole("button", { name: "Закрыть поиск" }).click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
    await expect(page.getByRole("combobox", { name: "Сообщение проекту" })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("project-chat-mobile.png"), fullPage: true });
});
