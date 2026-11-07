import type { Page } from "@playwright/test";
import type { FileSearchItem, ProjectFile } from "../../src/lib/types";
import { installMockApi as installBaseApi, mockState, stamp } from "./mockApi";

export function projectFile(overrides: Partial<ProjectFile> = {}): ProjectFile {
    return { id: 5, project_id: 1, uploaded_by_user_id: 1, original_name: "plan.pdf", content_type: "application/pdf", size: 1024, created_at: stamp, content_url: "/api/v1/projects/1/files/5/content", extraction_status: "ready", extraction_detail: null, index_status: "ready", index_detail: null, indexed_at: stamp, summary: "План запуска проекта", ...overrides };
}

export async function installProjectFilesApi(page: Page, initialFiles: ProjectFile[] = []) {
    const base = mockState();
    const state = {
        files: initialFiles,
        texts: new Map<number, string>([[5, "Первый фрагмент\n".padEnd(8000, "а") + "Второй фрагмент\nСогласовать запуск."]]),
        listError: false,
        detailError: false,
        retryError: false,
        deleteError: false,
        rejectedUploads: new Set<string>(),
        searchResults: null as FileSearchItem[] | null,
        requests: [] as { method: string; url: URL; body: string | null; contentType: string | undefined }[],
        errors: base.errors,
    };
    await page.route("https://fonts.googleapis.com/**", (route) => route.abort());
    await installBaseApi(page, base);
    await page.route("**/api/v1/**", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const method = request.method();
        if (method === "OPTIONS") return route.fallback();
        const headers = { "access-control-allow-origin": request.headers()["origin"] ?? "http://127.0.0.1:4179", "access-control-allow-credentials": "true" };
        const reply = (json: unknown, status = 200) => route.fulfill({ status, headers, ...(status === 204 ? {} : { json }) });
        if (url.pathname === "/api/v1/projects/1/documents") {
            return reply([{ id: 91, project_id: 1, slug: "decisions", title: "Решения проекта", content_md: "# Решения", created_at: stamp, updated_at: stamp, search_title: null, search_excerpt: null, search_match_source: null }]);
        }
        if (url.pathname === "/api/v1/projects/1/files/search") {
            state.requests.push({ method, url, body: request.postData(), contentType: request.headers()["content-type"] });
            const q = url.searchParams.get("q")?.toLocaleLowerCase("ru-RU") ?? "";
            const scope = url.searchParams.get("scope") ?? "all";
            const all: FileSearchItem[] = state.searchResults ?? state.files.map(file => ({ source_type: "project_file", source_id: file.id, project_id: 1, task_id: null, task_key: null, task_title: null, document_slug: null,
                title: file.original_name, caption: file.caption ?? null, content_type: file.content_type, preview_url: file.preview_url ?? null, excerpt: state.texts.get(file.id) ?? "", rank: 1 }));
            const items = all.filter(item => (!q || `${item.title} ${item.caption ?? ""} ${item.excerpt}`.toLocaleLowerCase("ru-RU").includes(q)) &&
                (scope === "all" || (scope === "project_files" && item.source_type === "project_file") || (scope === "task_files" && item.source_type === "task_attachment") || (scope === "documents" && item.source_type === "document")) &&
                (!url.searchParams.has("task_id") || item.task_id === Number(url.searchParams.get("task_id"))));
            const offset = Number(url.searchParams.get("offset") ?? 0);
            const limit = Number(url.searchParams.get("limit") ?? 20);
            return reply({ items: items.slice(offset, offset + limit), total: items.length, limit, offset });
        }
        const match = /^\/api\/v1\/projects\/1\/files(?:\/(\d+)(?:\/(retry|content|preview))?)?$/.exec(url.pathname);
        if (!match) return route.fallback();
        state.requests.push({ method, url, body: request.postData(), contentType: request.headers()["content-type"] });
        if (!match[1]) {
            if (method === "POST") {
                const name = /filename="([^"]+)"/.exec(request.postData() ?? "")?.[1] ?? "plan.pdf";
                if (state.rejectedUploads.has(name)) return reply({ detail: "Хранилище временно недоступно" }, 503);
                const caption = /name="caption"\r?\n\r?\n([^\r\n]*)/.exec(request.postData() ?? "")?.[1] ?? null;
                const created = projectFile({ id: 10 + state.files.length, original_name: name, caption, extraction_status: "pending", index_status: "pending", indexed_at: null, summary: null });
                state.files.unshift(created);
                state.texts.set(created.id, "Содержание нового файла.");
                return reply(created, 201);
            }
            if (state.listError) return reply({ detail: "Ошибка чтения файлов" }, 503);
            const search = url.searchParams.get("search")?.toLocaleLowerCase("ru-RU") ?? "";
            const offset = Number(url.searchParams.get("offset") ?? 0);
            const limit = Number(url.searchParams.get("limit") ?? 100);
            return reply(state.files.filter((file) => !search || file.original_name.toLocaleLowerCase("ru-RU").includes(search)).slice(offset, offset + limit));
        }
        const id = Number(match[1]);
        const file = state.files.find((item) => item.id === id);
        if (!file) return reply({ detail: "Файл не найден" }, 404);
        if (match[2] === "retry") {
            if (state.retryError) return reply({ detail: "Обработка временно недоступна" }, 503);
            Object.assign(file, { extraction_status: "pending", extraction_detail: null, index_status: "pending", index_detail: null });
            return reply(file, 202);
        }
        if (match[2] === "content") {
            const filename = encodeURIComponent(file.original_name).replace(/['()*]/g, char => `%${char.charCodeAt(0).toString(16).toUpperCase()}`);
            return route.fulfill({ status: 200, headers: { ...headers, "content-type": file.content_type, "content-disposition": `attachment; filename*=UTF-8''${filename}` }, body: "%PDF-1.4 mock original" });
        }
        if (match[2] === "preview") return route.fulfill({ status: 200, headers: { ...headers, "content-type": "image/svg+xml" }, body: '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="80"><rect width="100" height="80" fill="#5798c7"/><path d="M10 60 40 20 60 45 80 15" fill="none" stroke="white" stroke-width="4"/></svg>' });
        if (method === "PATCH") { Object.assign(file, request.postDataJSON()); return reply(file); }
        if (method === "DELETE") {
            if (state.deleteError) return reply({ detail: "Не удалось удалить оригинал" }, 503);
            state.files = state.files.filter((item) => item.id !== id);
            return reply(null, 204);
        }
        if (state.detailError) return reply({ detail: "Ошибка чтения текста" }, 503);
        const text = file.extraction_status === "ready" ? state.texts.get(id) ?? "" : "";
        const offset = Number(url.searchParams.get("offset") ?? 0);
        const size = Number(url.searchParams.get("max_chars") ?? 8000);
        return reply({ ...file, text: text.slice(offset, offset + size), text_total_chars: text.length, text_offset: offset, next_offset: offset + size < text.length ? offset + size : null });
    });
    return state;
}
