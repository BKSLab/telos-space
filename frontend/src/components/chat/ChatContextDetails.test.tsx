// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { queryKeys } from "@/lib/api";
import type { ChatContextTarget } from "@/lib/chatContext";
import type { DocumentDetail, ProjectFile, ProjectFileDetail, ProjectStage, Task } from "@/lib/types";
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
const document: DocumentDetail = {
    id: 7, project_id: 1, title: "Текущий документ", slug: "current-plan", content_md: "## Цели\n\n**План проекта** <img src=x onerror=alert(1)> <script>alert(2)</script>",
    created_at: stamp, updated_at: stamp, search_match_source: null, search_title: null, search_excerpt: null,
};
const file: ProjectFile = {
    id: 9, project_id: 1, uploaded_by_user_id: 1, original_name: "plan.pdf", content_type: "application/pdf", size: 1024,
    content_url: "https://untrusted.invalid/content", created_at: stamp, extraction_status: "ready", extraction_detail: null,
    index_status: "ready", index_detail: null, indexed_at: stamp, summary: null,
};

function target(type: ChatContextTarget["type"], id = type === "task" ? 5 : type === "document" ? 7 : 9): ChatContextTarget {
    return { type, id, title: "Прежнее название в ответе", excerpt: "Цитата из прежнего ответа", documentSlug: "old-slug" };
}

function mockApi() {
    const state = {
        tasks: new Map([[5, structuredClone(task)]]), documents: new Map([[7, structuredClone(document)]]),
        files: new Map([[9, structuredClone(file)]]), stages: [stage], failures: new Map<string, number>(),
        texts: new Map([[9, "Начало <b>без HTML</b>\n".padEnd(8000, "а") + "Последняя страница"]]),
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
        const documentMatch = /^\/api\/v1\/documents\/(\d+)$/.exec(url.pathname);
        if (documentMatch) return json(state.documents.get(Number(documentMatch[1])));
        const fileMatch = /^\/api\/v1\/projects\/1\/files\/(\d+)$/.exec(url.pathname);
        if (fileMatch) {
            const id = Number(fileMatch[1]);
            const current = state.files.get(id)!;
            const text = current.extraction_status === "ready" ? state.texts.get(id) ?? "" : "";
            const offset = Number(url.searchParams.get("offset") ?? 0);
            const size = Number(url.searchParams.get("max_chars") ?? 8000);
            return json({ ...current, text: text.slice(offset, offset + size), text_total_chars: text.length, text_offset: offset, next_offset: offset + size < text.length ? offset + size : null });
        }
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

it("показывает текущий документ через санитизированный Markdown и использует актуальный slug", async () => {
    const state = mockApi();
    const onOpenFull = vi.fn();
    const { container } = mount(target("document"), { onOpenFull });
    await screen.findByRole("heading", { name: "Текущий документ" });
    expect((await screen.findByRole("heading", { name: "Цели" })).tagName).toBe("H2");
    expect(screen.getByText("План проекта").tagName).toBe("STRONG");
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("[onerror]")).toBeNull();
    expect(screen.getByRole("link", { name: "Открыть документ" }).getAttribute("href")).toBe("/projects/DEMO/docs/current-plan");
    expect(screen.getByRole("region", { name: "Фрагмент из ответа" }).textContent).toContain("Цитата из прежнего ответа");
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(state.calls.every((call) => call.method === "GET")).toBe(true);
});

describe("границы проекта", () => {
    it.each(["task", "document", "project_file"] as const)("не раскрывает %s из другого проекта", async (type) => {
        const state = mockApi();
        if (type === "task") state.tasks.get(5)!.project_id = 2;
        if (type === "document") state.documents.get(7)!.project_id = 2;
        if (type === "project_file") state.files.get(9)!.project_id = 2;
        mount(target(type));
        await screen.findByText("Этот объект недоступен в текущем проекте.");
        expect(screen.queryByRole("heading")).toBeNull();
        expect(screen.queryByText("Цитата из прежнего ответа")).toBeNull();
        expect(screen.queryByRole("link")).toBeNull();
        expect(screen.queryByRole("button")).toBeNull();
        expect(state.calls.some((call) => call.url.pathname.endsWith("/stages"))).toBe(false);
    });
});

describe("отказ доступа после сохранённого ответа", () => {
    it.each([
        ["task", 403], ["task", 404], ["document", 403], ["document", 404], ["project_file", 403], ["project_file", 404],
    ] as const)("скрывает cached %s и цитату при %s, позволяет явно повторить чтение", async (type, status) => {
        const state = mockApi();
        const path = type === "task" ? "/api/v1/tasks/5" : type === "document" ? "/api/v1/documents/7" : "/api/v1/projects/1/files/9";
        state.failures.set(path, status);
        const cachedTitle = "Закрытые данные из кэша";
        mount(target(type), { seed: (client) => {
            if (type === "task") client.setQueryData([...queryKeys.task(5), "chat-preview", 1], { ...task, title: cachedTitle });
            if (type === "document") client.setQueryData(queryKeys.document(7), { ...document, title: cachedTitle });
            if (type === "project_file") client.setQueryData(queryKeys.projectFile(1, 9, 0), { ...file, original_name: cachedTitle, text: "Закрытый текст", text_total_chars: 13, text_offset: 0, next_offset: null } satisfies ProjectFileDetail);
        } });
        await screen.findByRole("alert");
        expect(screen.getByText("Объект удалён или у вас нет доступа к нему.")).toBeTruthy();
        expect(screen.queryByText(cachedTitle)).toBeNull();
        expect(screen.queryByText("Закрытый текст")).toBeNull();
        expect(screen.queryByText("Цитата из прежнего ответа")).toBeNull();
        expect(screen.queryByText(/Внутреннее пояснение/)).toBeNull();
        expect(screen.queryByRole("link")).toBeNull();
        expect(state.calls.filter((call) => call.url.pathname === path)).toHaveLength(1);
        state.failures.delete(path);
        fireEvent.click(screen.getByRole("button", { name: "Повторить загрузку" }));
        await screen.findByRole("heading", { name: type === "task" ? task.title : type === "document" ? document.title : file.original_name });
        expect(screen.getByRole("region", { name: "Фрагмент из ответа" })).toBeTruthy();
    });
});

it("читает файл постранично как plain text, отделяет цитату и сбрасывает страницу при выборе другого файла", async () => {
    const state = mockApi();
    const user = userEvent.setup();
    const { select } = mount(target("project_file"));
    const text = await screen.findByLabelText("Текст файла");
    expect(text.textContent).toContain("Начало <b>без HTML</b>");
    expect(text.querySelector("b")).toBeNull();
    expect(screen.getByRole("region", { name: "Фрагмент из ответа" }).textContent).toContain("Цитата из прежнего ответа");
    expect(screen.getByRole("link", { name: "Скачать оригинал" }).getAttribute("href")).toBe("http://localhost:8000/api/v1/projects/1/files/9/content");
    expect(screen.getByRole("link", { name: "Открыть в Документах" }).getAttribute("href")).toBe("/projects/DEMO/docs?file=9");
    await user.click(screen.getByRole("button", { name: "Далее" }));
    await waitFor(() => expect(screen.getByLabelText("Текст файла").textContent).toBe("Последняя страница"));
    expect((screen.getByRole("button", { name: "Далее" }) as HTMLButtonElement).disabled).toBe(true);
    expect(state.calls.some((call) => call.url.searchParams.get("offset") === "8000" && call.url.searchParams.get("max_chars") === "8000")).toBe(true);
    await user.click(screen.getByRole("button", { name: "Назад" }));
    await waitFor(() => expect(screen.getByLabelText("Текст файла").textContent).toContain("Начало"));
    await user.click(screen.getByRole("button", { name: "Далее" }));
    await waitFor(() => expect(screen.getByLabelText("Текст файла").textContent).toBe("Последняя страница"));
    state.files.set(10, { ...file, id: 10, original_name: "next.txt" });
    state.texts.set(10, "Другой файл с первой страницы");
    select(target("project_file", 10));
    await waitFor(() => expect(screen.getByLabelText("Текст файла").textContent).toBe("Другой файл с первой страницы"));
    expect(state.calls.at(-1)?.url.searchParams.get("offset")).toBe("0");
    expect(state.calls.every((call) => call.method === "GET")).toBe(true);
});

it.each([
    ["empty", "disabled", "Текст не найден", "Извлечённого текста нет."],
    ["pending", "pending", "Готовим текст", "Файл обрабатывается."],
    ["disabled", "disabled", "Распознавание отключено", "Распознавание изображения отключено."],
    ["failed", "failed", "Ошибка извлечения", "Извлечённого текста нет."],
] as const)("файл %s/%s объясняет состояние и сохраняет оригинал", async (extraction, index, label, description) => {
    const state = mockApi();
    Object.assign(state.files.get(9)!, { extraction_status: extraction, index_status: index, extraction_detail: "Пояснение обработки" });
    mount(target("project_file"));
    await screen.findByText(label);
    expect(screen.getByText("Пояснение обработки")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain(description);
    expect(screen.getByRole("link", { name: "Скачать оригинал" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Удалить|Повторить обработку/ })).toBeNull();
});

it("останавливает polling после ошибки и после завершения, явное чтение восстанавливает файл", async () => {
    vi.useFakeTimers();
    const state = mockApi();
    Object.assign(state.files.get(9)!, { extraction_status: "processing", index_status: "pending" });
    mount(target("project_file"));
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(screen.getByText("Извлекаем текст")).toBeTruthy();
    state.failures.set("/api/v1/projects/1/files/9", 403);
    await act(async () => { await vi.advanceTimersByTimeAsync(2010); });
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "plan.pdf" })).toBeNull();
    const count = state.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(8000); });
    expect(state.calls).toHaveLength(count);
    state.failures.clear();
    Object.assign(state.files.get(9)!, { extraction_status: "ready", index_status: "ready" });
    fireEvent.click(screen.getByRole("button", { name: "Повторить загрузку" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(screen.getByText("В знаниях Ариса")).toBeTruthy();
    const readyCount = state.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(8000); });
    expect(state.calls).toHaveLength(readyCount);
});
