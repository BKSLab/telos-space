// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import type { ChatMessage as Message } from "@/lib/projectChat";
import type { KnowledgeSource, Task } from "@/lib/types";
import { useUiStore } from "@/stores/ui";
import { ChatContextProvider } from "./ChatContextProvider";
import { ChatContextPanel } from "./ChatContextPanel";
import { ChatMessage } from "./ChatMessage";

vi.mock("@/lib/useProjectChat", () => ({
    useProjectChat: () => ({ projectId: 1, projectKey: "DEMO", userId: 1, canWrite: false, state: "connected" }),
}));
vi.mock("@/lib/useCalendarView", () => ({ useMediaQuery: () => true }));

const clients: QueryClient[] = [];
const task: Task = {
    id: 42, revision: 1, number: 42, project_id: 1, key: "DEMO-42", title: "Проверить оформление заказа",
    description_md: "Проверить обязательные поля формы.", stage_id: 2, priority: "MEDIUM", role: null,
    assignee: null, participants: [], start_date: null, due_date: null, baseline_start_date: null,
    baseline_due_date: null, completed_at: null, checklist: null, position: 0,
    created_at: "2026-11-14T10:00:00Z", updated_at: "2026-11-14T10:00:00Z", comments_count: 0,
    last_comment: null, search_match_source: null, search_title: null, search_excerpt: null, wbs_node_id: null,
};
const source: KnowledgeSource = { source_id: "task:42", entity_type: "task", entity_id: 42, task_id: 42,
    title: "DEMO-42 · Проверить оформление заказа", excerpt: "Согласовать сценарий приёмки.", score: null,
    is_primary: true, card: { key: "DEMO-42", status: "В работе", summary: task.description_md } };
function answer(patch: Partial<Message> = {}): Message {
    return { id: 7, chat_id: 1, project_id: 1, seq: 7, author_type: "AGENT", author: null, client_message_id: "answer-7",
        content: "Проверил задачу DEMO-42.", revision: 1, created_at: "2026-11-14T10:00:00Z", edited_at: null, deleted_at: null,
        reply: null, mentions: [], entities: [], attachments: [], reactions: [],
        agent: { name: "Арис", status: "completed", requester: null, request_message_id: 6, error: null, sources: [source], actions: [] }, ...patch };
}
function setup(message: Message) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    clients.push(client);
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async input => {
        const path = new URL(String(input), "http://localhost").pathname;
        const result = path === "/api/v1/tasks/42" ? task : path === "/api/v1/projects/1/stages"
            ? [{ id: 2, project_id: 1, name: "В работе", is_done_stage: false }] : null;
        return new Response(JSON.stringify(result), { status: result ? 200 : 404, headers: { "Content-Type": "application/json" } });
    });
    const tree = (row: Message) => <QueryClientProvider client={client}><MemoryRouter>
        <ChatContextProvider projectId={1} projectKey="DEMO" messages={[row]} enabled>
            <ChatMessage message={row} onReply={vi.fn()} onQuote={vi.fn()} onEdit={vi.fn()} onJump={vi.fn()} />
            <ChatContextPanel hidden={false} />
        </ChatContextProvider>
    </MemoryRouter></QueryClientProvider>;
    const view = render(tree(message));
    return { ...view, fetch, update: (row: Message) => view.rerender(tree(row)) };
}
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); useUiStore.getState().setSelectedTaskId(null); });

it("READ инициатор видит точную публичную тему и может разрешить внешнее исследование", async () => {
    const row = answer();
    row.agent!.requester = { id: 1, username: "reader", display_name: "Читатель" };
    row.agent!.actions = [{ id: "web-consent", tool_name: "telos_request_web_search", effect: "external_release", title: "Публичный поиск", arguments: { public_topic: "Python official documentation", topic_token: "server-token-hidden" }, status: "pending", result: {}, created_at: "2026-11-16T12:00:00Z" }];
    setup(row);
    expect((screen.getByRole("button", { name: "Разрешить" }) as HTMLButtonElement).disabled).toBe(false);
    expect(screen.getByText("Точная публичная тема")).toBeTruthy();
    expect(screen.getByText("Python official documentation")).toBeTruthy();
    expect(screen.queryByText("server-token-hidden")).toBeNull();
});

it("завершение реального server payload показывает карточку задачи и текущий контекст без открытия модалки", async () => {
    const pending = answer({ content: "", agent: { ...answer().agent!, status: "processing", sources: [] } });
    const { update, fetch } = setup(pending);
    expect(screen.getByText("Арис думает…")).toBeTruthy();
    expect(screen.queryByRole("button", { name: `Задача: ${source.title}` })).toBeNull();
    update(answer({ revision: 2 }));
    expect((await screen.findByRole("button", { name: `Задача: ${source.title}` })).getAttribute("aria-pressed")).toBe("true");
    const panel = screen.getByRole("complementary", { name: "Контекст чата" });
    expect(await within(panel).findByRole("heading", { name: task.title })).toBeTruthy();
    expect(within(panel).getByText(task.description_md!)).toBeTruthy();
    expect(within(panel).getByText(source.excerpt!)).toBeTruthy();
    expect(useUiStore.getState().selectedTaskId).toBeNull();
    expect(fetch.mock.calls.some(([url]) => /\/view|attention/.test(String(url)))).toBe(false);
});

it("единственная подтверждённая TASK entity выбирается при пустом sources", async () => {
    setup(answer({ entities: [{ entity_type: "TASK", entity_id: 42, available: true, title: task.title, status: "В работе", href: null, subtitle: null }],
        agent: { ...answer().agent!, sources: [] } }));
    expect(screen.getByRole("button", { name: `Задача: ${task.title}` }).getAttribute("aria-pressed")).toBe("true");
    expect(await screen.findByRole("heading", { name: task.title })).toBeTruthy();
});

it("карточка комментария выбирает server task_id и показывает его релевантный фрагмент", async () => {
    const comment: KnowledgeSource = { ...source, source_id: "comment:900", entity_type: "comment", entity_id: 900, title: "Уточнение приёмки", excerpt: "Проверить подтверждение по электронной почте." };
    setup(answer({ agent: { ...answer().agent!, sources: [comment] } }));
    expect(screen.getByRole("button", { name: "Комментарий: Уточнение приёмки" }).getAttribute("aria-pressed")).toBe("true");
    const panel = screen.getByRole("complementary", { name: "Контекст чата" });
    expect(await within(panel).findByRole("heading", { name: task.title })).toBeTruthy();
    expect(within(panel).getByText(comment.excerpt!)).toBeTruthy();
});

it("упоминание ключа в Markdown без подтверждённых источников не создаёт карточку или контекст", async () => {
    const { fetch } = setup(answer({ agent: { ...answer().agent!, sources: [] } }));
    expect(await screen.findByText("Проверил задачу DEMO-42.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Задача:/ })).toBeNull();
    expect(screen.queryByRole("heading", { name: task.title })).toBeNull();
    // The independent Today panel can read history; an unverified task mention must not fetch an entity.
    expect(fetch.mock.calls.every(([url]) => new URL(String(url), "http://local").pathname === "/api/v1/projects/1/history")).toBe(true);
});

it("общий чат выводит отдельную подтверждённую цитату из ChatAgentState", () => {
    setup(answer({ agent: { ...answer().agent!, sources: [], message_references: [{ kind: "project_chat", chat_id: 1, message_id: 4, project_id: 1, project_key: "DEMO", author_id: 2, author_name: "Олег", created_at: "2026-11-14T09:30:00Z", revision: 1, content_hash: "verified", quote: "Согласовали приёмку в пятницу.", start: 0, end: 29, offset_unit: "unicode_codepoints", state: "available" }] } }));
    expect(screen.getByRole("region", { name: "Цитаты сообщений" }).querySelector("blockquote")?.textContent).toBe("Согласовали приёмку в пятницу.");
    expect(screen.getByRole("link", { name: "Открыть сообщение" }).getAttribute("href")).toBe("/projects/DEMO/chat?message=4");
});
