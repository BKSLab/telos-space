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
    const state = { files: [] as ProjectFile[], uploads: [] as { file: File; headers: HeadersInit | undefined; credentials: RequestCredentials | undefined }[], listRequests: [] as URL[] };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
        const url = new URL(String(input));
        const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
        if (url.pathname.endsWith("/documents")) return json([{ id: 7, project_id: 1, slug: "plan", title: "План проекта", updated_at: stamp, search_match_source: null, search_title: null, search_excerpt: null }]);
        if (url.pathname.endsWith("/files")) {
            if (init?.method === "POST") {
                const file = (init.body as FormData).get("file") as File;
                state.uploads.push({ file, headers: init.headers, credentials: init.credentials });
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
    expect(await screen.findByRole("button", { name: "Открыть файл plan.pdf" })).toBeTruthy();
    expect(await screen.findByText(/«error.pdf»: Нет места для файла/)).toBeTruthy();
    expect(screen.getByText("Загружено файлов: 1")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Открыть файл error.pdf" })).toBeNull();
    expect(state.uploads.map((item) => item.file.name)).toEqual(["plan.pdf", "error.pdf"]);
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

it("передаёт серверу поисковый запрос для файлов и параметры первой страницы", async () => {
    const state = mockApi();
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Файлов пока нет");
    await user.type(screen.getByLabelText("Поиск документов и файлов проекта"), "бюджет & план");
    await waitFor(() => expect(state.listRequests.at(-1)?.searchParams.get("search")).toBe("бюджет & план"));
    expect(state.listRequests.at(-1)?.searchParams.get("limit")).toBe("100");
    expect(state.listRequests.at(-1)?.searchParams.get("offset")).toBe("0");
});
