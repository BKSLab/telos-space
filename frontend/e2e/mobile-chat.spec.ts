import { expect, test, type Locator, type Page } from "@playwright/test";
import type { ChatAttachment, ChatMessage, ChatMessageCreate } from "../src/lib/projectChat";
import { installMockApi, mockState, stamp } from "./support/mockApi";

test.use({ isMobile: true, hasTouch: true, viewport: { width: 390, height: 844 } });
test.setTimeout(60_000);

async function setup(page: Page) {
    const state = mockState();
    await installMockApi(page, state);
    const author = { id: state.me.id, username: state.me.username, display_name: "Тестов Тест" };
    const file: ChatAttachment = { id: "mobile-file", original_name: "Очень длинное название приложения к сообщению.md", content_type: "text/markdown", size_bytes: 12, message_id: null, created_at: stamp };
    const rows: ChatMessage[] = Array.from({ length: 45 }, (_, index) => ({
        id: index + 1, chat_id: 1, project_id: 1, seq: index + 1, author_type: "USER", author,
        client_message_id: `seed-${index}`, content: index === 29 ? "Согласование мобильной версии" : `Обсуждение ${index + 1}. ${"Подробности для команды. ".repeat(8)}`,
        revision: 1, created_at: stamp, edited_at: null, deleted_at: null, reply: null, mentions: [], entities: [], reactions: [],
        attachments: index === 44 ? [{ ...file, id: "preview", original_name: `${"ДлинноеНазвание".repeat(15)}.png`, content_type: "image/png", message_id: index + 1 }] : [],
    }));
    const commands: { type: string; data: Record<string, unknown> }[] = [];
    await page.route(/\/api\/v1\/projects\/\d+\/chat(?:\/|\?|$)/, async route => {
        const url = new URL(route.request().url());
        const [, project, rest] = /\/projects\/(\d+)\/chat(.*)/.exec(url.pathname)!;
        const projectId = Number(project);
        const headers = { "access-control-allow-origin": "http://127.0.0.1:4179", "access-control-allow-credentials": "true", "access-control-allow-headers": "content-type", "access-control-allow-methods": "GET, POST, DELETE, OPTIONS" };
        if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
        const reply = (json: unknown) => route.fulfill({ json, headers });
        if (!rest) return reply({ id: projectId, project_id: projectId, event_cursor: 45, last_read_seq: 45, unread_count: 0, members: [author] });
        if (rest === "/messages") return reply({ messages: rows, has_more: false, event_cursor: 45 });
        if (rest === "/events") return reply({ events: [], has_more: false, cursor: 45 });
        if (rest === "/search") return reply({ messages: rows.filter(row => row.content.includes(url.searchParams.get("q") ?? "")), has_more: false, event_cursor: 45 });
        if (rest === "/attachments") return reply(file);
        if (rest.endsWith("/content")) return route.fulfill({ headers, contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jT1sAAAAASUVORK5CYII=", "base64") });
        if (route.request().method() === "DELETE") return route.fulfill({ status: 204, headers });
        return reply([]);
    });
    await page.routeWebSocket("**/projects/*/chat/ws", socket => {
        const projectId = Number(/\/projects\/(\d+)\/chat\/ws/.exec(new URL(socket.url()).pathname)![1]);
        socket.send(JSON.stringify({ type: "ready", project_id: projectId, data: { heartbeat_seconds: 20, can_write: true, presence: [author.id], typing: [] } }));
        socket.onMessage(raw => {
            const command = JSON.parse(String(raw));
            commands.push({ type: command.type, data: command.data ?? {} });
            let result: object = {};
            if (command.type === "message.send") {
                const data = command.data as ChatMessageCreate;
                const id = rows.length + 1;
                const message: ChatMessage = { ...rows[0], id, seq: id, client_message_id: data.client_message_id, content: data.content, attachments: [], reply: null };
                rows.push(message);
                socket.send(JSON.stringify({ type: "message.created", project_id: projectId, seq: id, data: { message_id: id, message } }));
                result = { message, client_message_id: data.client_message_id };
            } else if (command.type === "read.set") result = { last_read_seq: rows.length, unread_count: 0 };
            socket.send(JSON.stringify({ type: "ack", request_id: command.request_id, data: result }));
        });
    });
    return { state, rows, commands };
}

async function touchTarget(locator: Locator) {
    await expect(locator).toBeInViewport();
    const box = await locator.boundingBox();
    expect(box!.width).toBeGreaterThanOrEqual(43);
    expect(box!.height).toBeGreaterThanOrEqual(43);
}
async function noPageOverflow(page: Page) {
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1)).toBe(true);
}

for (const width of [320, 390, 768]) {
    test(`личный и общий ввод, файлы и черновики на touch ${width}px`, async ({ page }, info) => {
        const server = await setup(page);
        await page.setViewportSize({ width, height: 844 });
        await page.goto("/");
        await touchTarget(page.getByRole("button", { name: "Открыть чаты", exact: true }));
        await page.getByRole("button", { name: "Открыть чаты", exact: true }).tap();
        const panel = page.getByRole("dialog", { name: "Чаты", exact: true });
        const question = panel.getByRole("textbox", { name: "Вопрос Арису", exact: true });
        await expect(question).toBeVisible();
        await expect(question).not.toBeFocused();
        await question.fill("Личный вопрос");
        await question.press("Enter");
        await expect(question).toHaveValue("Личный вопрос\n");
        expect(server.state.aris.submissions).toHaveLength(0);
        await panel.locator('input[type="file"]').setInputFiles({ name: "acceptance.md", mimeType: "text/markdown", buffer: Buffer.from("мобильный файл") });
        await expect(panel.getByRole("button", { name: "Убрать acceptance.md" })).toBeVisible();
        await touchTarget(panel.getByRole("button", { name: "Свернуть чат", exact: true }));
        await panel.getByRole("button", { name: "Свернуть чат", exact: true }).tap();
        await page.getByRole("button", { name: "Открыть чаты", exact: true }).tap();
        await expect(question).toHaveValue("Личный вопрос\n");
        await panel.getByRole("button", { name: "Открыть чат во вкладке" }).tap();
        await expect(page).toHaveURL(/\/chat$/);
        const personalSend = page.getByRole("button", { name: "Отправить Арису (Enter)" });
        await expect(personalSend).toBeVisible();
        await expect(personalSend).toBeEnabled();
        await touchTarget(personalSend);
        if (width === 320) await page.screenshot({ path: info.outputPath("personal-chat-320.png"), animations: "disabled" });
        await personalSend.tap();
        await expect(page.getByText(server.state.aris.answer, { exact: true })).toBeVisible();
        expect(server.state.aris.submissions[0].content).toBe("Личный вопрос");
        expect(server.state.aris.submissions[0].file_ids).toHaveLength(1);
        await page.getByRole("combobox", { name: "Выбор чата", exact: true }).selectOption("project:1");
        const common = page.getByRole("combobox", { name: "Сообщение проекту", exact: true });
        await common.fill("Командное сообщение");
        await common.press("Enter");
        await expect(common).toHaveValue("Командное сообщение\n");
        expect(server.commands.filter(command => command.type === "message.send")).toHaveLength(0);
        await common.fill("Командное сообщение\nВторая строка");
        await touchTarget(page.getByRole("button", { name: "Отправить", exact: true }));
        await page.getByRole("button", { name: "Отправить", exact: true }).tap();
        await expect(common).toHaveValue("");
        expect(server.commands.find(command => command.type === "message.send")?.data.content).toBe("Командное сообщение\nВторая строка");
        await noPageOverflow(page);
        expect(server.state.errors).toEqual([]);
    });
}

test("поиск, ответ, реакции и длинные вложения доступны в окне и landscape", async ({ page }, info) => {
    const server = await setup(page);
    await page.goto("/projects/DEMO/team");
    await page.getByRole("button", { name: "Открыть чаты", exact: true }).tap();
    const panel = page.getByRole("dialog", { name: "Чаты", exact: true });
    await expect(panel.getByRole("combobox", { name: "Сообщение проекту", exact: true })).toBeVisible();
    await page.screenshot({ path: info.outputPath("floating-project-chat-390.png"), animations: "disabled" });
    await panel.getByRole("button", { name: "Поиск в чате" }).tap();
    const search = panel.getByRole("complementary", { name: "Поиск по сообщениям" });
    await touchTarget(search.getByRole("button", { name: "Закрыть поиск" }));
    await search.getByRole("searchbox", { name: "Поиск по сообщениям" }).fill("Согласование");
    await search.getByRole("button").filter({ hasText: "Согласование мобильной версии" }).tap();
    await expect(search).toHaveCount(0);
    const message = panel.locator("#chat-message-30");
    await touchTarget(message.getByRole("button", { name: "Действия с сообщением" }));
    await message.getByRole("button", { name: "Действия с сообщением" }).tap();
    await panel.getByRole("button", { name: "Реакция 👍" }).tap();
    expect(server.commands.some(command => command.type === "reaction.set")).toBe(true);
    await message.getByRole("button", { name: "Действия с сообщением" }).tap();
    await panel.getByRole("button", { name: "Ответить", exact: true }).tap();
    await expect(panel.getByRole("button", { name: "Убрать ответ" })).toBeVisible();
    await panel.locator('input[type="file"]').setInputFiles({ name: "notes.md", mimeType: "text/markdown", buffer: Buffer.from("заметки") });
    const remove = panel.getByRole("button", { name: `Убрать файл Очень длинное название приложения к сообщению.md` });
    await touchTarget(remove);
    await remove.tap();
    await expect(remove).toHaveCount(0);
    await panel.getByLabel("История сообщений проекта").evaluate(element => { element.scrollTop = element.scrollHeight; });
    await expect(panel.getByRole("button", { name: /Посмотреть изображение/ })).toBeInViewport();
    await noPageOverflow(page);
    await page.setViewportSize({ width: 844, height: 390 });
    const bounds = await panel.boundingBox();
    expect(bounds!.x).toBe(0);
    expect(bounds!.width).toBe(844);
    expect(bounds!.height).toBe(390);
    await touchTarget(panel.getByRole("button", { name: "Свернуть чат", exact: true }));
    await touchTarget(panel.getByRole("button", { name: "Прикрепить файлы" }));
    await expect(panel.getByRole("button", { name: "Убрать ответ" })).toBeVisible();
    await expect(panel.locator(".chat-selector-privacy")).toBeVisible();
    await expect(panel.locator("#chat-composer-hint")).toBeHidden();
    await expect.poll(() => panel.getByLabel("История сообщений проекта").evaluate(element => {
        const bounds = element.getBoundingClientRect();
        const frame = element.closest("#floating-chats")!.getBoundingClientRect();
        return Math.max(0, Math.min(bounds.bottom, frame.bottom, window.innerHeight) - Math.max(bounds.top, frame.top, 0));
    })).toBeGreaterThanOrEqual(100);
    await page.screenshot({ path: info.outputPath("floating-project-chat-landscape.png"), animations: "disabled" });
    await panel.getByRole("button", { name: "Убрать ответ" }).tap();
    await expect(panel.getByRole("button", { name: "Убрать ответ" })).toHaveCount(0);
    await noPageOverflow(page);
    expect(server.state.errors).toEqual([]);
});

test("личная история остаётся на читаемом месте, когда Арис завершает ответ", async ({ page }) => {
    const server = await setup(page);
    const conversation = { id: 1, title: "Планирование", created_at: stamp, updated_at: stamp };
    server.state.aris.conversations.push(conversation);
    server.state.aris.pending = true;
    server.state.aris.messages.set(1, Array.from({ length: 35 }, (_, index) => ({
        id: index + 1, conversation_id: 1, request_id: `request-${index}`, role: "assistant", content: `Ответ ${index + 1}. ${"Договорённости по проекту. ".repeat(8)}`,
        sources: [], actions: [], files: [], page_context: null, status: index === 34 ? "processing" : "completed", error: null, created_at: stamp,
    })));
    await page.goto("/chat");
    const history = page.getByLabel("История личного диалога", { exact: true });
    await expect(history.getByText("Арис думает…")).toBeVisible();
    await history.evaluate(element => { element.scrollTop = 140; element.dispatchEvent(new Event("scroll")); });
    await expect(page.getByRole("button", { name: "К последним сообщениям" })).toBeVisible();
    const before = await history.evaluate(element => element.scrollTop);
    server.state.aris.pending = false;
    await expect(history.getByText(server.state.aris.answer, { exact: true })).toHaveCount(1);
    expect(await history.evaluate(element => element.scrollTop)).toBeCloseTo(before, 0);
    await page.getByRole("button", { name: "К последним сообщениям" }).tap();
    await expect(history.getByText(server.state.aris.answer, { exact: true })).toBeInViewport();
    await expect(page.getByRole("button", { name: "К последним сообщениям" })).toHaveCount(0);
    expect(server.state.errors).toEqual([]);
});
