import { expect, test, type Page, type WebSocketRoute } from "@playwright/test";
import { randomUUID } from "node:crypto";
import type { ChatAgentState, ChatAttachment, ChatEntity, ChatEvent, ChatMessage, ChatMessageCreate } from "../src/lib/projectChat";

test.setTimeout(60_000);

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
    const entity: ChatEntity = { entity_type: "DOCUMENT", entity_id: 1, title: "План тестирования", available: true, status: null, subtitle: null, href: "/projects/DEMO/docs/testing" };
    let seq = 0;
    let messageId = 0;
    const control = { dropNextAck: false, connections: 0, messageGets: 0, blocked: false, commands: [] as { type: string; data: Record<string, unknown> }[] };
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
    const add = (content: string, userId = 2, broadcast = true) => create(userId, { content, mentions: [], entities: [], attachment_ids: [], client_message_id: randomUUID(), reply_to_message_id: null }, broadcast);
    const addTim = (content: string, status: ChatAgentState["status"] = "completed") => create(0, { content, mentions: [], entities: [], attachment_ids: [], client_message_id: randomUUID(), reply_to_message_id: null }, true, { author_type: "AGENT", author: null, agent: { name: "Арис", status, requester: members[0], request_message_id: 1, error: null, sources: [], actions: [] } });

    const attach = async (page: Page, userId = 1) => {
        const publicUser = members.find((user) => user.id === userId)!;
        const user = { ...publicUser, first_name: userId === 1 ? "Анна" : "Олег", last_name: userId === 1 ? "Иванова" : "Петров", middle_name: null, is_active: true, has_avatar: false };
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
            if (path.endsWith("/chat/entities") || path.endsWith("/chat/entities/resolve")) return reply([entity]);
            if (path.endsWith("/chat/attachments") && method === "POST") {
                const file: ChatAttachment = { id: randomUUID(), original_name: "acceptance.txt", size_bytes: 12, content_type: "text/plain", message_id: null, created_at: stamp };
                files.set(file.id, file); return reply(file, 201);
            }
            if (path.includes("/chat/attachments/") && method === "DELETE") return route.fulfill({ status: 204, headers });
            if (path.includes("/documents/") || path.includes("/docs/")) return reply({ id: 1, project_id: 1, slug: "testing", title: entity.title, content_md: "Критерии готовности интерфейса.", created_at: stamp, updated_at: stamp });
            return reply([]);
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
    return { attach, add, addTim, emit, rows, entity, sockets, control };
}

const LEGACY_CONFIRM = "0f1e2d3c-0000-4000-8000-000000000001";

async function installPersonalHistory(page: Page) {
    const stamp = "2026-09-21T10:00:00Z";
    const state = { reindexCalls: 0 };
    await page.route(/\/api\/v1\/(?:projects\/1\/(?:agent\/conversations|knowledge\/)|agent\/pending\/|tim\/conversations)/, async (route) => {
        const request = route.request();
        const path = new URL(request.url()).pathname;
        const headers = { "access-control-allow-origin": "http://127.0.0.1:4179", "access-control-allow-credentials": "true", "access-control-allow-headers": "content-type", "access-control-allow-methods": "GET, POST, OPTIONS" };
        const reply = (json: unknown) => route.fulfill({ json, headers });
        if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers });
        if (path === "/api/v1/tim/conversations") return reply({ items: [], next_offset: null });
        if (path.endsWith("/agent/conversations")) return reply({ items: [{ id: 17, project_id: 1, title: "План проекта", created_at: stamp, updated_at: stamp }], next_offset: null });
        if (path.endsWith("/agent/conversations/17/messages")) return reply({ items: [{ id: 170, conversation_id: 17, request_id: "history", role: "assistant", content: "Сохранённый план проверки проекта", sources: [], files: [], actions: [], status: "completed", error: null, created_at: stamp }], next_before_id: null });
        if (path.endsWith("/knowledge/reindex")) { state.reindexCalls += 1; return reply({ queued: true }); }
        if (path.endsWith("/knowledge/status")) return reply({ enabled: true, ready: true, points_count: 23, pending_jobs: 0, processing_jobs: 0, failed_jobs: 1, last_error: null, obsolete_points: 0, coverage: [{ entity_type: "document", total: 2, indexed: 1, missing: 1, stale: 0 }], file_issues: [{ source_id: "attachment:9", title: "План.pdf", status: "failed", detail: "Текст файла недоступен" }] });
        if (path.endsWith(`/agent/pending/${LEGACY_CONFIRM}`)) return reply({ id: LEGACY_CONFIRM, tool_name: "delete_task", title: "Удалить задачу", status: "pending", arguments: { task_id: 42 }, preview: {}, result: {}, project: { id: 1, key: "DEMO", name: "Портал заказов" }, created_at: stamp, expires_at: "2026-10-01T10:00:00Z" });
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

test("проектный чат занимает рабочую ширину на 1920 px и не переполняет экран 320 px", async ({ page }, info) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    const server = chatServer();
    server.add("Сообщение на широком экране", 2, false);
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
    const editor = (await input.boundingBox())!;
    expect(tabs.width, "Навигация проекта использует ширину рабочей области").toBeGreaterThan(workspace.width - 64);
    expect(Math.abs(firstTab.x - tabs.x)).toBeLessThanOrEqual(2);
    expect(Math.abs(lastTab.x + lastTab.width - tabs.x - tabs.width), "Вкладки распределены по всей ширине навигации").toBeLessThanOrEqual(2);
    expect(messages.width, "История сообщений использует ширину рабочей области").toBeGreaterThan(workspace.width - 64);
    expect(editor.width, "Редактор использует ширину рабочей области").toBeGreaterThan(workspace.width - 80);
    await page.screenshot({ path: info.outputPath("project-chat-wide.png") });
    await page.setViewportSize({ width: 320, height: 844 });
    await assertActiveTabFullyVisible();
    await page.reload();
    await expect(page.getByText("Подключено", { exact: true })).toBeVisible();
    await assertProjectChatOnly();
    await expect(sections.getByRole("link", { name: "Чат", exact: true })).toBeInViewport();
    await assertActiveTabFullyVisible();
    await expect(input).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), "Узкая страница не прокручивается по горизонтали").toBe(true);
    await input.fill("Проверка чата на экране 320 px");
    await expect(page.getByRole("button", { name: "Отправить", exact: true })).toBeInViewport();
    await page.getByRole("button", { name: "Отправить", exact: true }).click();
    await expect(page.getByRole("article").filter({ hasText: "Проверка чата на экране 320 px" })).toBeVisible();
    expect(server.control.commands.filter((command) => command.type === "message.send").at(-1)?.data.content).toBe("Проверка чата на экране 320 px");
    await assertActiveTabFullyVisible();
    await page.screenshot({ path: info.outputPath("project-chat-320.png") });
});

test("старый адрес личного диалога сохраняет историю, подтверждение и контекст в едином чате", async ({ page }) => {
    const server = chatServer(); await server.attach(page); await installPersonalHistory(page);
    await page.goto(`/projects/DEMO/agent?conversation=17&confirm=${LEGACY_CONFIRM}&task=42&tag=a&tag=b#context`);
    await expect(page).toHaveURL(/\/projects\/DEMO\/chat\?/);
    const confirmation = page.getByRole("dialog", { name: "Подтверждение действия агента", exact: true });
    await expect(confirmation).toBeVisible();
    const redirected = new URL(page.url());
    expect(redirected.searchParams.get("view")).toBe("personal");
    expect(redirected.searchParams.get("confirm")).toBe(LEGACY_CONFIRM);
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
    await page.getByRole("button", { name: "Общий чат", exact: true }).click();
    await expect(page.getByRole("combobox", { name: "Сообщение проекту" })).toBeVisible();
    await expect(page.getByText("Сохранённый план проверки проекта", { exact: true })).toHaveCount(0);
});

test("знания и переиндексация доступны из единого чата на телефоне", async ({ page }) => {
    const server = chatServer(); await server.attach(page); const state = await installPersonalHistory(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/projects/DEMO/knowledge?task=42#knowledge");
    await expect(page).toHaveURL(/\/projects\/DEMO\/chat\?task=42&view=knowledge#knowledge$/);
    expect(new URL(page.url()).searchParams.get("task")).toBe("42");
    expect(new URL(page.url()).hash).toBe("#knowledge");
    const knowledge = page.getByRole("complementary", { name: "Знания проекта", exact: true });
    await expect(knowledge.getByText("Фрагментов", { exact: true })).toBeVisible();
    await expect(knowledge.getByText("23", { exact: true })).toBeVisible();
    await expect(knowledge.getByText("План.pdf", { exact: true })).toBeVisible();
    await knowledge.getByRole("button", { name: "Переиндексировать", exact: true }).click();
    await expect.poll(() => state.reindexCalls).toBe(1);
    await page.goto("/chat");
    await page.getByRole("button", { name: "Проектные диалоги и знания", exact: true }).click();
    await expect(page.getByRole("link", { name: "DEMO · Знания", exact: true })).toHaveAttribute("href", "/projects/DEMO/chat?view=knowledge");
    await page.getByRole("link", { name: "DEMO · Личные диалоги", exact: true }).click();
    await expect(page.getByText("Сохранённый план проверки проекта", { exact: true })).toBeVisible();
    expect(new URL(page.url()).searchParams.get("view")).toBe("personal");
    expect(new URL(page.url()).searchParams.get("conversation")).toBe("17");
    await expect(page.getByRole("combobox", { name: "Выбор чата", exact: true })).toHaveCount(0);
});

test("окно команды и вкладка разделяют сокет, историю, ответ и черновик редактирования", async ({ page }) => {
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
    await page.getByRole("navigation", { name: "Основная навигация" }).getByRole("link", { name: "Проекты", exact: true }).click();
    await expect(page).toHaveURL(/\/projects$/);
    await expect(page.getByRole("dialog", { name: "Чаты", exact: true })).toBeVisible();
    await expect(page.getByText("Проверено из двух представлений", { exact: true })).toBeVisible();
    expect(server.control.connections).toBe(connections);
    await page.screenshot({ path: "test-results/floating-team-chat.png" });
});

test("поиск и прочтение в окне команды не меняют основной экран, окно помещается на телефоне", async ({ page }) => {
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
    await page.screenshot({ path: "test-results/floating-team-mobile.png" });
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
    server.emit("entity.updated", { entity_type: "DOCUMENT", entity_id: 1 });
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
    server.addTim("", "processing");
    await expect(page.getByRole("complementary", { name: "Новое сообщение проекта" })).toHaveCount(0);
    server.addTim("План проверки готов");
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
