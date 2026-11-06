import type { Page } from "@playwright/test";
import type { ProjectFile } from "../../src/lib/types";
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
        const match = /^\/api\/v1\/projects\/1\/files(?:\/(\d+)(?:\/(retry|content))?)?$/.exec(url.pathname);
        if (!match) return route.fallback();
        state.requests.push({ method, url, body: request.postData(), contentType: request.headers()["content-type"] });
        if (!match[1]) {
            if (method === "POST") {
                const name = /filename="([^"]+)"/.exec(request.postData() ?? "")?.[1] ?? "plan.pdf";
                if (state.rejectedUploads.has(name)) return reply({ detail: "Хранилище временно недоступно" }, 503);
                const created = projectFile({ id: 10 + state.files.length, original_name: name, extraction_status: "pending", index_status: "pending", indexed_at: null, summary: null });
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
