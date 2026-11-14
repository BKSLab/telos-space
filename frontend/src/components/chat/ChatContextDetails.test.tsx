// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { queryKeys } from "@/lib/api";
import type { ChatContextTarget } from "@/lib/chatContext";
import type { DocumentFragment, ProjectStage, Task } from "@/lib/types";
import { documentFixture, documentTextFixture } from "@/components/documents/documentFixtures.test-utils";
import { useUiStore } from "@/stores/ui";
import { ChatContextDetails } from "./ChatContextDetails";

const stamp = "2026-11-06T10:00:00Z";
const clients: QueryClient[] = [];
const stage: ProjectStage = { id: 2, project_id: 1, name: "Согласование", color: "#58a6ff", order_index: 0, is_done_stage: false };
const task: Task = {
    revision: 1,
    id: 5, project_id: 1, stage_id: 2, key: "DEMO-5", number: 5, title: "Текущее название задачи",
    description_md: "**Важное описание** <script>alert('unsafe')</script> [опасная ссылка](javascript:alert%281%29)",
    priority: "HIGH", role: null, assignee: "Прежний исполнитель",
    participants: [{ id: 1, role: "EXECUTOR", user: { id: 3, username: "anna", first_name: "Анна", last_name: "Примерова", middle_name: null, has_avatar: false } }],
    start_date: null, due_date: "2026-12-12", baseline_start_date: null, baseline_due_date: null, completed_at: null,
    position: 0, created_at: stamp, updated_at: stamp, comments_count: 0, last_comment: null,
    search_match_source: null, search_title: null, search_excerpt: null, wbs_node_id: null,
    checklist: { title: "Приёмка", items: Array.from({ length: 8 }, (_, index) => ({ id: String(index), text: `Пункт ${index + 1}`, is_completed: index < 2 })) },
};
const documentRef = "d1_cHJvamVjdF9maWxlOjk";
const document = documentFixture({ ref: documentRef, title: "Текущий документ", capabilities: { can_read: true, can_edit: false, can_delete: false, can_retry: false, can_link: false, can_detach: false } });

function target(type: ChatContextTarget["type"]): ChatContextTarget {
    return type === "task" ? { type, id: 5, title: "Прежнее название в ответе", excerpt: "Цитата из прежнего ответа" }
        : { type, documentRef, title: "Прежнее название в ответе" };
}

function mockApi() {
    const state = {
        tasks: new Map([[5, structuredClone(task)]]), document: structuredClone(document),
        stages: [stage], failures: new Map<string, number>(),
        text: documentTextFixture({ ref: documentRef, text: "😀 Согласованное условие" }),
        calls: [] as { method: string; url: URL }[],
    };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
        const url = new URL(String(input), "http://localhost");
        state.calls.push({ url, method: init?.method ?? "GET" });
        const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
        const failure = state.failures.get(url.pathname);
        if (failure) return json({ detail: "Внутреннее пояснение, которое не должно раскрыться при отказе" }, failure);
        if (url.pathname.endsWith("/stages")) return json(state.stages);
        const taskMatch = /^\/api\/v1\/tasks\/(\d+)$/.exec(url.pathname);
        if (taskMatch) return json(state.tasks.get(Number(taskMatch[1])));
        if (url.pathname === `/api/v1/documents/${documentRef}`) return json(state.document);
        if (url.pathname === `/api/v1/documents/${documentRef}/text`) return json(state.text);
        return json({ detail: "Не найдено" }, 404);
    });
    return state;
}

function mount(initialTarget = target("task"), options: { onOpenFull?: () => void; seed?: (client: QueryClient) => void } = {}) {
    // Ретрай клиента включён специально: сам preview обязан отключить его.
    const client = new QueryClient({ defaultOptions: { queries: { retry: 1, retryDelay: 1, gcTime: Infinity } } });
    clients.push(client);
    options.seed?.(client);
    const tree = (current: ChatContextTarget) => <QueryClientProvider client={client}><MemoryRouter>
        <ChatContextDetails target={current} projectId={1} projectKey="DEMO" onOpenFull={options.onOpenFull} />
    </MemoryRouter></QueryClientProvider>;
    const view = render(tree(initialTarget));
    return { client, ...view, select: (current: ChatContextTarget) => view.rerender(tree(current)) };
}

afterEach(() => {
    cleanup();
    clients.splice(0).forEach((client) => client.clear());
    useUiStore.getState().setSelectedTaskId(null);
    vi.restoreAllMocks();
    vi.useRealTimers();
});

it("читает задачу без attention, показывает текущую стадию и readonly данные; открывает полную карточку после callback", async () => {
    const state = mockApi();
    const user = userEvent.setup();
    const onOpenFull = vi.fn(() => expect(useUiStore.getState().selectedTaskId).toBeNull());
    const fullCardCache = { ...task, title: "Кэш полной карточки", attention_view: { revision: 3 } };
    const { client, container } = mount(target("task"), { onOpenFull, seed: (cache) => cache.setQueryData(queryKeys.task(5), fullCardCache) });
    await screen.findByRole("heading", { name: "Текущее название задачи" });
    expect(await screen.findByText("Согласование")).toBeTruthy();
    expect(screen.getByText("DEMO-5")).toBeTruthy();
    expect(screen.getByText("Примерова Анна")).toBeTruthy();
    expect(screen.queryByText("Прежний исполнитель")).toBeNull();
    expect(screen.getByText("Высокий")).toBeTruthy();
    expect(screen.getByTitle("Срок: 12 декабря 2026 г.")).toBeTruthy();
    expect((await screen.findByText("Важное описание")).tagName).toBe("STRONG");
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
    const checklist = screen.getByRole("region", { name: "Чек-лист задачи" });
    expect(within(checklist).getAllByRole("listitem")).toHaveLength(6);
    expect(within(checklist).getByText("Выполнено 2 из 8")).toBeTruthy();
    expect(within(checklist).getByText(/Ещё пунктов: 2/)).toBeTruthy();
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(client.getQueryData(queryKeys.task(5))).toEqual(fullCardCache);
    expect(client.getQueryData([...queryKeys.task(5), "chat-preview", 1])).toMatchObject({ title: task.title });
    await user.click(screen.getByRole("button", { name: "Открыть карточку задачи" }));
    expect(onOpenFull).toHaveBeenCalledOnce();
    expect(useUiStore.getState().selectedTaskId).toBe(5);
    expect(state.calls.some((call) => /attention|view/.test(call.url.pathname))).toBe(false);
    expect(state.calls.every((call) => call.method === "GET")).toBe(true);
});

it("передаёт единый ref и проверенный Unicode фрагмент общему просмотрщику", async () => {
    const state = mockApi();
    HTMLElement.prototype.scrollIntoView = vi.fn();
    const fragment: DocumentFragment = { document_ref: documentRef, rendition: "markdown", content_hash: state.text.content_hash,
        start: 2, end: Array.from(state.text.text).length, offset_unit: "unicode_codepoints", quote: "Согласованное условие" };
    const { container } = mount({ type: "document", documentRef, title: "Устаревший заголовок", fragment });
    await screen.findByText("Показан фрагмент текущей версии документа.");
    expect(container.querySelector("mark")?.textContent).toBe(fragment.quote);
    expect(screen.getByRole("link", { name: "Открыть полностью" }).getAttribute("href")).toBe(`/projects/DEMO/docs/${documentRef}`);
    expect(useUiStore.getState().selectedTaskId).toBeNull();
    expect(state.calls.every(call => call.method === "GET")).toBe(true);
});

it.each(["task", "document"] as const)("не раскрывает %s после переноса в другой проект", async type => {
    const state = mockApi();
    if (type === "task") state.tasks.get(5)!.project_id = 2;
    else state.document.ownership.project_id = 2;
    mount(target(type));
    await screen.findByText(type === "task" ? "Этот объект недоступен в текущем проекте." : "Документ недоступен в этом проекте");
    expect(screen.queryByText("Согласованное условие")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
    expect(state.calls.some(call => call.url.pathname.endsWith("/text") || call.url.pathname.endsWith("/stages"))).toBe(false);
});

it.each([403, 404])("скрывает прежние данные документа при отказе %s и разрешает явный повтор", async status => {
    const state = mockApi();
    state.failures.set(`/api/v1/documents/${documentRef}`, status);
    mount(target("document"), { seed: client => client.setQueryData(queryKeys.document(documentRef), { ...document, title: "Закрытые данные из кэша" }) });
    await screen.findByRole("alert");
    expect(screen.queryByText("Закрытые данные из кэша")).toBeNull();
    expect(screen.queryByText(/Внутреннее пояснение/)).toBeNull();
    expect(state.calls.some(call => call.url.pathname.endsWith("/text"))).toBe(false);
    state.failures.clear();
    fireEvent.click(screen.getByRole("button", { name: "Повторить загрузку документа" }));
    await screen.findByRole("heading", { name: "Текущий документ" });
});
