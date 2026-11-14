// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { documentFixture, documentPageFixture, documentTextFixture } from "@/components/documents/documentFixtures.test-utils";
import { ProjectDocumentsPage } from "./ProjectDocumentsPage";

const clients: QueryClient[] = [];
HTMLElement.prototype.scrollIntoView = vi.fn();
function setup() {
    const state = { requests: [] as URL[], uploads: [] as { file: File; caption: FormDataEntryValue | null; id: FormDataEntryValue | null; credentials: string | undefined }[], creates: [] as Record<string, unknown>[], items: [documentFixture(), documentFixture({ ref: "task-owned", title: "Документ задачи", source_kind: "upload", tasks: [{ id: 5, key: "TEST-5", title: "Согласование", relation: "owner" }] })] };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
        const url = new URL(String(input));
        const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
        if (url.pathname === "/api/v1/projects/1/tasks") return json([{ id: 5, key: "TEST-5", title: "Согласование" }]);
        if (url.pathname === "/api/v1/documents/upload") {
            const body = init!.body as FormData; const file = body.get("file") as File;
            state.uploads.push({ file, caption: body.get("caption"), id: body.get("request_id"), credentials: init?.credentials });
            if (file.name === "error.pdf") return json({ detail: "Нет места для документа" }, 503);
            const created = documentFixture({ ref: `opaque-${file.name}`, source_kind: "upload", title: file.name, original: { filename: file.name, content_type: file.type, size: file.size, content_url: "/api/v1/documents/opaque/content", preview_url: null } });
            state.items.push(created); return json(created, 201);
        }
        if (url.pathname === "/api/v1/documents") {
            if (init?.method === "POST") { const body = JSON.parse(String(init.body)); state.creates.push(body); const created = documentFixture({ ref: "created-markdown", title: body.title }); state.items.push(created); return json(created, 201); }
            state.requests.push(url);
            const offset = Number(url.searchParams.get("offset") ?? 0);
            const filtered = url.searchParams.get("task_id") ? state.items.filter(item => item.tasks.some(task => task.id === 5)) : state.items;
            return json(documentPageFixture(filtered, { total: offset === 20 ? 21 : 21, offset, next_offset: offset === 0 ? 20 : null }));
        }
        if (url.pathname.endsWith("/text")) return json(documentTextFixture({ ref: url.pathname.split("/").at(-2)! }));
        if (url.pathname.startsWith("/api/v1/documents/")) return json(state.items.find(item => url.pathname.endsWith(item.ref)) ?? documentFixture());
        throw new Error(`Unexpected request: ${url.pathname}`);
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/projects/TEST/docs"]}><Routes><Route element={<Outlet context={{ project: { id: 1, key: "TEST" } }} />}><Route path="/projects/TEST/docs" element={<ProjectDocumentsPage />} /></Route></Routes></MemoryRouter></QueryClientProvider>);
    return { state, user: userEvent.setup() };
}
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); });

it("фильтрует весь каталог без q, сбрасывает страницу при изменении фильтра и сохраняет кодированный поиск", async () => {
    const { state, user } = setup();
    await screen.findByRole("button", { name: "Открыть документ План проекта" });
    await user.click(screen.getByRole("button", { name: "Далее" }));
    await waitFor(() => expect(state.requests.at(-1)?.searchParams.get("offset")).toBe("20"));
    await user.selectOptions(screen.getByLabelText("Принадлежность документа"), "tasks");
    await waitFor(() => expect(state.requests.at(-1)?.searchParams.get("scope")).toBe("tasks"));
    expect(state.requests.at(-1)?.searchParams.has("q")).toBe(false);
    expect(state.requests.at(-1)?.searchParams.get("offset")).toBe("0");
    await user.selectOptions(screen.getByLabelText("Задача документа"), "5");
    await waitFor(() => expect(state.requests.at(-1)?.searchParams.get("task_id")).toBe("5"));
    await user.selectOptions(screen.getByLabelText("Состояние обработки"), "failed");
    await waitFor(() => expect(state.requests.at(-1)?.searchParams.get("processing")).toBe("failed"));
    await user.type(screen.getByLabelText("Поиск документов проекта"), "бюджет & план");
    await waitFor(() => expect(state.requests.at(-1)?.searchParams.get("q")).toBe("бюджет & план"));
    await user.click(screen.getAllByRole("button", { name: "Сбросить фильтры" })[0]);
    await waitFor(() => expect(state.requests.at(-1)?.searchParams.has("task_id")).toBe(false));
});

it("использует одну карточку для Markdown и документа задачи, открывает общий просмотр", async () => {
    const { user } = setup();
    await user.click(await screen.findByRole("button", { name: "Открыть документ Документ задачи" }));
    const modal = await screen.findByRole("dialog", { name: "Документ" });
    expect(within(modal).getByRole("tab", { name: "Текст" })).toBeTruthy();
    expect(screen.getByText("TEST-5")).toBeTruthy();
});

it("при частичной загрузке сохраняет только неуспешный документ и повторяет его UUID", async () => {
    const { state, user } = setup();
    await user.click(await screen.findByRole("button", { name: "Добавить документ" }));
    await user.click(screen.getByRole("tab", { name: "Загрузить" }));
    await user.upload(screen.getByLabelText("Выбрать документы", { selector: "input" }), [new File(["План"], "plan.pdf", { type: "application/pdf" }), new File(["Ошибка"], "error.pdf", { type: "application/pdf" })]);
    await user.type(screen.getByLabelText("Подпись к plan.pdf"), "Версия для встречи");
    await user.click(screen.getByRole("button", { name: "Добавить" }));
    await screen.findByText(/«error.pdf»: Нет места для документа/);
    expect(screen.queryByLabelText("Подпись к plan.pdf")).toBeNull();
    expect(screen.getByLabelText("Подпись к error.pdf")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Добавить" }));
    await waitFor(() => expect(state.uploads).toHaveLength(3));
    expect(state.uploads.map(item => item.file.name)).toEqual(["plan.pdf", "error.pdf", "error.pdf"]);
    expect(state.uploads[1].id).toBe(state.uploads[2].id);
    expect(state.uploads[0].caption).toBe("Версия для встречи");
    expect(state.uploads.every(item => item.credentials === "include")).toBe(true);
    expect(state.creates).toEqual([]);
});

it("создаёт Markdown через общий API с текстом и владельцем", async () => {
    const { state, user } = setup();
    await user.click(await screen.findByRole("button", { name: "Добавить документ" }));
    await user.type(screen.getByLabelText("Название документа"), "Решения встречи");
    await user.type(screen.getByLabelText("Текст документа · Markdown"), "# Решения\n\nДоговорились");
    await user.click(screen.getByRole("button", { name: "Добавить" }));
    await waitFor(() => expect(state.creates).toHaveLength(1));
    expect(state.creates[0]).toEqual(expect.objectContaining({ title: "Решения встречи", content_md: "# Решения\n\nДоговорились", project_id: 1 }));
    expect(state.creates[0].request_id).toEqual(expect.any(String));
    expect(state.creates[0]).not.toHaveProperty("owner_task_id");
});

it("отклоняет неподдерживаемый и пустой оригинал до HTTP", async () => {
    const { state } = setup(); const user = userEvent.setup({ applyAccept: false });
    await user.click(await screen.findByRole("button", { name: "Добавить документ" }));
    await user.click(screen.getByRole("tab", { name: "Загрузить" }));
    const input = screen.getByLabelText("Выбрать документы", { selector: "input" });
    await user.upload(input, new File(["zip"], "archive.zip"));
    expect(screen.getByText(/archive.zip.*не поддерживается/)).toBeTruthy();
    await user.upload(input, new File([], "empty.txt"));
    expect(screen.getByText(/empty.txt.*пуст/)).toBeTruthy();
    expect(state.uploads).toEqual([]);
});
