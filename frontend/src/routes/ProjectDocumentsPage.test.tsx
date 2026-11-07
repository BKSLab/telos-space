// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import type { ProjectFile } from "@/lib/types";
import { ProjectDocumentsPage } from "./ProjectDocumentsPage";

const stamp = "2026-11-06T10:00:00Z";
const clients: QueryClient[] = [];
function renderPage() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    clients.push(client);
    return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/projects/TEST/docs"]}>
        <Routes><Route element={<Outlet context={{ project: { id: 1, key: "TEST" } }} />}>
            <Route path="/projects/TEST/docs" element={<ProjectDocumentsPage />} />
        </Route></Routes>
    </MemoryRouter></QueryClientProvider>);
}

function mockApi() {
    const state = { files: [] as ProjectFile[], uploads: [] as { file: File; caption: FormDataEntryValue | null; headers: HeadersInit | undefined; credentials: RequestCredentials | undefined }[], listRequests: [] as URL[], searchRequests: [] as URL[] };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
        const url = new URL(String(input));
        const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
        if (url.pathname.endsWith("/documents")) return json([{ id: 7, project_id: 1, slug: "plan", title: "План проекта", updated_at: stamp, search_match_source: null, search_title: null, search_excerpt: null }]);
        if (url.pathname.endsWith("/tasks")) return json([{ id: 5, key: "TEST-5", title: "Согласование" }]);
        if (url.pathname.endsWith("/files/search")) {
            state.searchRequests.push(url);
            return json({ items: [], total: 0, limit: 20, offset: 0 });
        }
        if (url.pathname.endsWith("/files")) {
            if (init?.method === "POST") {
                const file = (init.body as FormData).get("file") as File;
                state.uploads.push({ file, caption: (init.body as FormData).get("caption"), headers: init.headers, credentials: init.credentials });
                if (file.name === "error.pdf") return json({ detail: "Нет места для файла" }, 503);
                const created: ProjectFile = { id: 5, project_id: 1, uploaded_by_user_id: 1, original_name: file.name, content_type: file.type, size: file.size, created_at: stamp, content_url: "/api/v1/projects/1/files/5/content", extraction_status: "ready", extraction_detail: null, index_status: "ready", index_detail: null, indexed_at: stamp, summary: null };
                state.files.push(created);
                return json(created, 201);
            }
            state.listRequests.push(url);
            return json(state.files);
        }
        return json([]);
    });
    return state;
}

afterEach(() => {
    cleanup();
    clients.splice(0).forEach((client) => client.clear());
    vi.restoreAllMocks();
});

it("загружает multipart с cookie, сохраняет успешный файл и Markdown при частичной ошибке", async () => {
    const state = mockApi();
    const user = userEvent.setup();
    renderPage();
    expect((await screen.findByRole("link", { name: /План проекта/ })).getAttribute("href")).toBe("/projects/TEST/docs/plan");
    await user.upload(screen.getByLabelText("Загрузить файл"), [
        new File(["Текст плана"], "plan.pdf", { type: "application/pdf" }),
        new File(["Не загрузился"], "error.pdf", { type: "application/pdf" }),
    ]);
    await user.type(screen.getByLabelText("Подпись к plan.pdf"), "План встречи");
    await user.click(screen.getByRole("button", { name: "Загрузить" }));
    await screen.findAllByText(/«error.pdf»: Нет места для файла/);
    // В диалоге остался только неуспешный файл; успешный не отправится повторно.
    expect(screen.queryByLabelText("Подпись к plan.pdf")).toBeNull();
    expect(screen.getByLabelText("Подпись к error.pdf")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Отмена" }));
    expect(await screen.findByRole("button", { name: "Открыть файл plan.pdf" })).toBeTruthy();
    expect(await screen.findByText(/«error.pdf»: Нет места для файла/)).toBeTruthy();
    expect(screen.getByText("Загружено файлов: 1")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Открыть файл error.pdf" })).toBeNull();
    expect(state.uploads.map((item) => item.file.name)).toEqual(["plan.pdf", "error.pdf"]);
    expect(state.uploads[0].caption).toBe("План встречи");
    expect(state.uploads.every((item) => item.credentials === "include")).toBe(true);
    expect(state.uploads.every((item) => !(item.headers as Record<string, string>)["Content-Type"])).toBe(true);
    expect(screen.getByRole("link", { name: /План проекта/ })).toBeTruthy();
});

it("валидирует размер, пустой файл, расширение и лимит партии до HTTP", async () => {
    const state = mockApi();
    const user = userEvent.setup({ applyAccept: false });
    renderPage();
    await screen.findByText("Файлов пока нет");
    const input = screen.getByLabelText("Загрузить файл");
    await user.upload(input, new File([new Uint8Array(10 * 1024 * 1024 + 1)], "large.pdf"));
    expect(screen.getByText(/large.pdf.*превышает 10 МБ/)).toBeTruthy();
    await user.upload(input, new File([], "empty.txt"));
    expect(screen.getByText(/empty.txt.*пуст/)).toBeTruthy();
    await user.upload(input, new File(["zip"], "archive.zip"));
    expect(screen.getByText(/archive.zip.*не поддерживается/)).toBeTruthy();
    await user.upload(input, Array.from({ length: 11 }, (_, index) => new File(["text"], `${index}.txt`)));
    expect(screen.getByText("Можно выбрать не более 10 файлов.")).toBeTruthy();
    expect(state.uploads).toEqual([]);
});

it("ищет по общему API, сохраняя отдельный список самостоятельных файлов проекта", async () => {
    const state = mockApi();
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Файлов пока нет");
    await user.type(screen.getByLabelText("Поиск документов и файлов проекта"), "бюджет & план");
    await waitFor(() => expect(state.searchRequests.at(-1)?.searchParams.get("q")).toBe("бюджет & план"));
    expect(state.searchRequests.at(-1)?.searchParams.get("scope")).toBe("all");
    expect(state.searchRequests.at(-1)?.searchParams.get("limit")).toBe("20");
    expect(state.searchRequests.at(-1)?.searchParams.get("offset")).toBe("0");
    expect(state.listRequests.at(-1)?.searchParams.has("search")).toBe(false);
    expect(state.listRequests.at(-1)?.searchParams.get("offset")).toBe("0");
});

it.each(["project_files", "task_files", "documents"])("фильтр %s без текста показывает подсказку и не отправляет пустой поиск", async scope => {
    const state = mockApi();
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Файлов пока нет");
    await user.selectOptions(screen.getByLabelText("Источники поиска"), scope);
    expect(await screen.findByText("Введите текст для поиска с выбранными фильтрами")).toBeTruthy();
    expect(screen.getByRole("link", { name: /План проекта/ })).toBeTruthy();
    expect(state.searchRequests).toEqual([]);
    await user.type(screen.getByLabelText("Поиск документов и файлов проекта"), "   ");
    expect(state.searchRequests).toEqual([]);
    await user.type(screen.getByLabelText("Поиск документов и файлов проекта"), "план");
    await waitFor(() => expect(state.searchRequests.at(-1)?.searchParams.get("q")).toBe("план"));
    expect(state.searchRequests.at(-1)?.searchParams.get("scope")).toBe(scope);
    expect(state.searchRequests.every(request => Boolean(request.searchParams.get("q")?.trim()))).toBe(true);
});

it("выбор задачи без текста не запускает поиск; введённый запрос использует выбранную задачу", async () => {
    const state = mockApi();
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("option", { name: "TEST-5 · Согласование" });
    await user.selectOptions(screen.getByLabelText("Задача — источник файла"), "5");
    expect(await screen.findByText("Введите текст для поиска с выбранными фильтрами")).toBeTruthy();
    expect(screen.getByRole("link", { name: /План проекта/ })).toBeTruthy();
    expect(state.searchRequests).toEqual([]);
    const search = screen.getByLabelText("Поиск документов и файлов проекта");
    await user.type(search, "бюджет");
    await waitFor(() => expect(state.searchRequests.at(-1)?.searchParams.get("q")).toBe("бюджет"));
    expect(state.searchRequests.at(-1)?.searchParams.get("task_id")).toBe("5");
    await user.clear(search);
    expect(await screen.findByText("Введите текст для поиска с выбранными фильтрами")).toBeTruthy();
    expect(state.searchRequests.every(request => Boolean(request.searchParams.get("q")?.trim()))).toBe(true);
});
