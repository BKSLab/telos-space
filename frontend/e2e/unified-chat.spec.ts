import { expect, test, type Page, type WebSocketRoute } from "@playwright/test";
import type { ChatEvent, ChatMessage, ChatMessageCreate } from "../src/lib/projectChat";
import { installMockApi, mockState, stamp, type MockState } from "./support/mockApi";

test.setTimeout(60_000);

/** API личных диалогов и проектные сокеты сохраняют разные истории. */
async function setup(page: Page, patch: (state: MockState) => void = () => undefined) {
    const state = mockState();
    patch(state);
    await installMockApi(page, state);
    const rows = new Map<number, ChatMessage[]>();
    const events = new Map<number, ChatEvent[]>();
    const active = new Map<WebSocketRoute, number>();
    const commands: { projectId: number; type: string; data: Record<string, unknown> }[] = [];
    const control = { connections: 0 };
    const author = { id: state.me.id, username: state.me.username, display_name: "Тестов Тест" };
    let nextId = 0;
    const messages = (projectId: number) => rows.get(projectId) ?? [];
    const projectEvents = (projectId: number) => events.get(projectId) ?? [];

    await page.route(/\/api\/v1\/projects\/\d+\/chat(?:\/|\?|$)/, async (route) => {
        const url = new URL(route.request().url());
        const match = /\/projects\/(\d+)\/chat(.*)/.exec(url.pathname)!;
        const projectId = Number(match[1]);
        const rest = match[2];
        const headers = { "access-control-allow-origin": "http://127.0.0.1:4179", "access-control-allow-credentials": "true", "access-control-allow-headers": "content-type", "access-control-allow-methods": "GET, POST, OPTIONS" };
        if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
        const reply = (json: unknown) => route.fulfill({ json, headers });
        const cursor = projectEvents(projectId).length;
        if (!rest) return reply({ id: projectId, project_id: projectId, event_cursor: cursor, last_read_seq: cursor, unread_count: 0, members: [author] });
        if (rest === "/messages") return reply({ messages: messages(projectId), has_more: false, event_cursor: cursor });
        if (rest === "/events") return reply({ events: projectEvents(projectId).filter((event) => event.seq! > Number(url.searchParams.get("after") ?? 0)), has_more: false, cursor });
        if (rest === "/search") return reply({ messages: messages(projectId).filter((message) => message.content.includes(url.searchParams.get("q") ?? "")), has_more: false, event_cursor: cursor });
        return reply([]);
    });
    await page.routeWebSocket("**/projects/*/chat/ws", (socket) => {
        const projectId = Number(/\/projects\/(\d+)\/chat\/ws/.exec(new URL(socket.url()).pathname)![1]);
        active.set(socket, projectId);
        control.connections += 1;
        socket.onClose(() => { active.delete(socket); });
        socket.send(JSON.stringify({ type: "ready", project_id: projectId, data: { heartbeat_seconds: 20, can_write: true, presence: [author.id], typing: [] } }));
        socket.onMessage((raw) => {
            const command = JSON.parse(String(raw));
            commands.push({ projectId, type: command.type, data: command.data ?? {} });
            let result: object = {};
            if (command.type === "message.send") {
                const data = command.data as ChatMessageCreate;
                const seq = projectEvents(projectId).length + 1;
                const message: ChatMessage = { id: ++nextId, chat_id: projectId, project_id: projectId, seq, author_type: "USER", author, client_message_id: data.client_message_id, content: data.content, revision: 1, created_at: stamp, edited_at: null, deleted_at: null, reply: null, mentions: [], entities: [], attachments: [], reactions: [] };
                rows.set(projectId, [...messages(projectId), message]);
                const event: ChatEvent = { type: "message.created", project_id: projectId, seq, data: { message_id: message.id, message } };
                events.set(projectId, [...projectEvents(projectId), event]);
                for (const [peer, peerProject] of active) if (peerProject === projectId) peer.send(JSON.stringify(event));
                result = { message, client_message_id: data.client_message_id };
            } else if (command.type === "read.set") result = { last_read_seq: projectEvents(projectId).length, unread_count: 0 };
            socket.send(JSON.stringify({ type: "ack", request_id: command.request_id, data: result }));
        });
    });
    return { state, rows, active, control, commands };
}

async function openChats(page: Page) {
    const button = page.getByRole("button", { name: "Открыть чаты", exact: true });
    await expect(button).toHaveCount(1);
    await button.click();
    const panel = page.getByRole("dialog", { name: "Чаты", exact: true });
    await expect(panel).toBeVisible();
    return panel;
}

async function expectFullPage(page: Page, personal: boolean) {
    await expect(page.getByRole("button", { name: "Открыть чаты", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Спросить Ариса", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Открыть чат команды", exact: true })).toHaveCount(0);
    await expect(page.getByRole("dialog", { name: "Чаты", exact: true })).toHaveCount(0);
    await expect(page.getByRole("combobox", { name: "Выбор чата", exact: true })).toHaveCount(personal ? 1 : 0);
    if (!personal) {
        await expect(page.getByRole("button", { name: "Личные диалоги и знания", exact: true })).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Проектные диалоги и знания", exact: true })).toHaveCount(0);
    }
}

test("одна компактная кнопка открывает личный чат и помнит выбор после перезагрузки", async ({ page }, info) => {
    const server = await setup(page);
    await page.goto("/projects");
    await expect(page.getByRole("button", { name: "Спросить Ариса", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Открыть чат команды", exact: true })).toHaveCount(0);
    await expect(page.getByRole("combobox", { name: "Выбор чата", exact: true })).toHaveCount(0);
    const button = await page.getByRole("button", { name: "Открыть чаты", exact: true }).boundingBox();
    expect(button!.width).toBeLessThanOrEqual(160);
    expect(button!.height).toBeLessThanOrEqual(64);
    let panel = await openChats(page);
    const selector = panel.getByRole("combobox", { name: "Выбор чата", exact: true });
    await expect(selector).toHaveValue("personal");
    await expect(selector.locator("option")).toHaveText(["Личный — Арис", "DEMO · Учебный проект", "SAMPLE · Личный план", "DRAFT · Черновик"]);
    await expect(panel.getByRole("textbox", { name: "Вопрос Арису" })).toBeVisible();
    await page.screenshot({ path: info.outputPath("unified-personal.png") });
    await expect(page.getByRole("dialog")).toHaveCount(1);
    await selector.selectOption("project:2");
    await expect(panel.getByRole("combobox", { name: "Сообщение проекту" })).toBeVisible();
    await panel.getByRole("button", { name: "Свернуть чат", exact: true }).click();
    await page.reload();
    await expect(page.getByRole("dialog", { name: "Чаты", exact: true })).toHaveCount(0);
    panel = await openChats(page);
    await expect(panel.getByRole("combobox", { name: "Выбор чата", exact: true })).toHaveValue("project:2");
    await expect(panel.getByRole("combobox", { name: "Сообщение проекту" })).toBeVisible();
    await expect.poll(() => server.active.size).toBe(1);
    expect([...server.active.values()]).toEqual([2]);
    expect(server.state.errors).toEqual([]);
});

test("личный и общий черновики переживают переключение и раскрытие во вкладку", async ({ page }) => {
    const server = await setup(page);
    await page.goto("/projects/DEMO/team");
    const panel = await openChats(page);
    const selector = panel.getByRole("combobox", { name: "Выбор чата", exact: true });
    await expect(selector).toHaveValue("project:1");
    await panel.getByRole("combobox", { name: "Сообщение проекту" }).fill("Общий черновик команды");
    await selector.selectOption("personal");
    await panel.getByRole("textbox", { name: "Вопрос Арису" }).fill("Личный черновик для Ариса");
    await selector.selectOption("project:1");
    await expect(panel.getByRole("combobox", { name: "Сообщение проекту" })).toHaveValue("Общий черновик команды");
    await selector.selectOption("personal");
    await expect(panel.getByRole("textbox", { name: "Вопрос Арису" })).toHaveValue("Личный черновик для Ариса");
    await selector.selectOption("project:1");
    const connections = server.control.connections;
    await panel.getByRole("button", { name: "Открыть чат во вкладке" }).click();
    await expect(page).toHaveURL(/\/projects\/DEMO\/chat$/);
    await expectFullPage(page, false);
    await expect(page.getByRole("combobox", { name: "Сообщение проекту" })).toHaveValue("Общий черновик команды");
    expect(server.control.connections).toBe(connections);
    await page.getByRole("link", { name: "Команда", exact: true }).click();
    const reopened = await openChats(page);
    await reopened.getByRole("combobox", { name: "Выбор чата", exact: true }).selectOption("personal");
    await expect(reopened.getByRole("textbox", { name: "Вопрос Арису" })).toHaveValue("Личный черновик для Ариса");
    await reopened.getByRole("button", { name: "Открыть чат во вкладке" }).click();
    await expect(page).toHaveURL(/\/chat$/);
    await expect(page.getByRole("textbox", { name: "Вопрос Арису" })).toHaveValue("Личный черновик для Ариса");
    await expect(page.getByRole("combobox", { name: "Сообщение проекту" })).toHaveCount(0);
    await expectFullPage(page, true);
    await page.getByRole("combobox", { name: "Выбор чата", exact: true }).selectOption("project:1");
    await expect(page).toHaveURL(/\/projects\/DEMO\/chat$/);
    await expect(page.getByRole("combobox", { name: "Сообщение проекту" })).toHaveValue("Общий черновик команды");
    await expectFullPage(page, false);
    await expect.poll(() => server.active.size).toBe(1);
    expect([...server.active.values()]).toEqual([1]);
    expect(server.control.connections).toBe(connections + 1);
    expect(server.state.tim.submissions).toHaveLength(0);
    expect(server.commands.filter((command) => command.type === "message.send")).toHaveLength(0);
    expect(server.state.errors).toEqual([]);
});

test("открытый чат закреплён за выбранным проектом при переходе в другой проект", async ({ page }, info) => {
    const server = await setup(page);
    await page.goto("/projects/DEMO/team");
    const panel = await openChats(page);
    await expect(panel.getByRole("combobox", { name: "Выбор чата", exact: true })).toHaveValue("project:1");
    await panel.getByRole("combobox", { name: "Сообщение проекту" }).fill("Черновик первого проекта");
    await page.screenshot({ path: info.outputPath("unified-project.png") });
    const connections = server.control.connections;
    await page.getByRole("navigation", { name: "Основная навигация" }).getByRole("link", { name: "Проекты", exact: true }).click();
    await page.locator('a[href="/projects/SAMPLE"]').first().click();
    await expect(page).toHaveURL(/\/projects\/SAMPLE$/);
    await expect(panel.getByRole("combobox", { name: "Выбор чата", exact: true })).toHaveValue("project:1");
    await expect(panel.getByRole("combobox", { name: "Сообщение проекту" })).toHaveValue("Черновик первого проекта");
    await expect.poll(() => server.active.size).toBe(1);
    expect([...server.active.values()]).toEqual([1]);
    expect(server.control.connections).toBe(connections);
    await panel.getByRole("button", { name: "Отправить", exact: true }).click();
    await expect(panel.getByRole("article").filter({ hasText: "Черновик первого проекта" })).toBeVisible();
    expect(server.commands.filter((command) => command.type === "message.send").map((command) => command.projectId)).toEqual([1]);
    expect(server.rows.get(2) ?? []).toEqual([]);
    expect(server.state.errors).toEqual([]);
});

test("личные вопросы отправляются в личный API и не появляются в общей переписке", async ({ page }) => {
    const server = await setup(page);
    await page.goto("/projects");
    const panel = await openChats(page);
    const selector = panel.getByRole("combobox", { name: "Выбор чата", exact: true });
    await panel.getByRole("textbox", { name: "Вопрос Арису" }).fill("Личное поручение Арису");
    await panel.getByRole("button", { name: "Отправить Арису (Enter)" }).click();
    await expect(panel.getByText(server.state.tim.answer, { exact: true })).toBeVisible();
    expect(server.state.tim.submissions.map((submission) => submission.content)).toEqual(["Личное поручение Арису"]);
    expect(server.commands.filter((command) => command.type === "message.send")).toHaveLength(0);
    await selector.selectOption("project:1");
    await expect(panel.getByRole("combobox", { name: "Сообщение проекту" })).toBeVisible();
    await expect(panel.getByText("Личное поручение Арису", { exact: true })).toHaveCount(0);
    await expect(panel.getByText(server.state.tim.answer, { exact: true })).toHaveCount(0);
    await panel.getByRole("combobox", { name: "Сообщение проекту" }).fill("Общее сообщение команды");
    await panel.getByRole("button", { name: "Отправить", exact: true }).click();
    await expect(panel.getByRole("article").filter({ hasText: "Общее сообщение команды" })).toBeVisible();
    expect(server.rows.get(1)?.map((message) => message.content)).toEqual(["Общее сообщение команды"]);
    await selector.selectOption("personal");
    await expect(panel.locator("p", { hasText: "Личное поручение Арису" })).toBeVisible();
    await expect(panel.getByText("Общее сообщение команды", { exact: true })).toHaveCount(0);
    await panel.getByRole("button", { name: "Открыть чат во вкладке" }).click();
    await expect(page).toHaveURL(/\/chat$/);
    await expectFullPage(page, true);
    await expect(page.getByRole("combobox", { name: "Выбор чата", exact: true })).toHaveValue("personal");
    await expect(page.locator("p", { hasText: "Личное поручение Арису" })).toBeVisible();
    await page.getByRole("combobox", { name: "Выбор чата", exact: true }).selectOption("project:2");
    await expect(page).toHaveURL(/\/projects\/SAMPLE\/chat$/);
    await expectFullPage(page, false);
    await expect(page.getByText("Личное поручение Арису", { exact: true })).toHaveCount(0);
    expect(server.state.errors).toEqual([]);
});

test("прежний view=vera открывает /chat, сохраняя query и hash", async ({ page }) => {
    const server = await setup(page);
    await page.goto("/projects/DEMO/chat?view=vera&tag=a&tag=b#private-context");
    await expect(page).toHaveURL(/\/chat\?tag=a&tag=b#private-context$/);
    const url = new URL(page.url());
    expect(url.searchParams.has("view")).toBe(false);
    expect(url.searchParams.getAll("tag")).toEqual(["a", "b"]);
    expect(url.hash).toBe("#private-context");
    await expectFullPage(page, true);
    await expect(page.getByRole("textbox", { name: "Вопрос Арису" })).toBeVisible();
    await expect(page.getByRole("combobox", { name: "Сообщение проекту" })).toHaveCount(0);
    expect(server.state.errors).toEqual([]);
});

test("полный личный чат доступен на телефоне без единого проекта", async ({ page }, info) => {
    const server = await setup(page, (state) => { state.projects = []; });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/chat");
    await expectFullPage(page, true);
    const selector = page.getByRole("combobox", { name: "Выбор чата", exact: true });
    await expect(selector).toHaveValue("personal");
    await expect(selector.locator("option")).toHaveText(["Личный — Арис"]);
    await page.getByRole("textbox", { name: "Вопрос Арису" }).fill("Что мне сделать первым?");
    await expect(page.getByRole("button", { name: "Отправить Арису (Enter)" })).toBeInViewport();
    await page.getByRole("button", { name: "Отправить Арису (Enter)" }).click();
    await expect(page.getByText(server.state.tim.answer, { exact: true })).toBeVisible();
    await page.screenshot({ path: info.outputPath("unified-mobile.png") });
    expect(server.state.tim.submissions).toHaveLength(1);
    expect(server.control.connections).toBe(0);
    expect(server.state.errors).toEqual([]);
});
