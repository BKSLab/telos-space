import type { Page } from "@playwright/test";
import type { DocumentListItem, TaskComment } from "../../src/lib/types";
import { documentFixture, documentPageFixture, documentTextFixture } from "../../src/components/documents/documentFixtures.test-utils";
import { installMockApi, mockState } from "./mockApi";

export async function installDocumentsApi(page: Page, items?: DocumentListItem[]) {
    const base = mockState();
    const plan = documentFixture({ ref: "opaque-plan", title: "План проекта" });
    const uploaded = documentFixture({ ref: "opaque-image", title: "Очень-длинное-название-схемы-проекта-без-пробелов-для-проверки-переносов.png", source_kind: "upload", format: "png", caption: "Согласованный вариант", original: { filename: "Схема.png", content_type: "image/png", size: 140, content_url: "/api/v1/documents/opaque-image/content", preview_url: "/api/v1/documents/opaque-image/preview" } });
    const task = documentFixture({ ref: "opaque-task", title: "Требования задачи", ownership: { kind: "task", project_id: 1, owner_task_id: 100 }, tasks: [{ id: 100, key: "DEMO-1", title: base.tasks[0].title, relation: "owner" }], capabilities: { ...plan.capabilities, can_link: false } });
    const state = { documents: items ?? [plan, uploaded, task], texts: new Map<string, string>(), comments: [] as TaskComment[], requests: [] as URL[], writes: [] as { path: string; method: string; body: string }[], uploads: [] as string[], commentSubmissions: [] as string[], failNextComments: 0, errors: base.errors };
    for (const document of state.documents) state.texts.set(document.ref, "# План 🧭\n\n**Согласовано**\n\nРабота по проекту.");
    const receipts = new Map<string, DocumentListItem>();
    await installMockApi(page, base);
    await page.route("**/api/v1/**", async route => {
        const request = route.request(); const url = new URL(request.url()); const method = request.method(); const path = url.pathname;
        const headers = { "access-control-allow-origin": request.headers()["origin"] ?? "http://127.0.0.1:4179", "access-control-allow-credentials": "true", "access-control-allow-headers": "content-type", "access-control-allow-methods": "GET, POST, PATCH, DELETE, OPTIONS" };
        const reply = (json: unknown, status = 200) => route.fulfill({ status, headers, ...(status === 204 ? {} : { json }) });
        const raw = request.postData() ?? "";
        if (method === "OPTIONS") return reply(null, 204);
        if (path.startsWith("/api/v1/documents") && method === "GET") state.requests.push(url);
        if (path.startsWith("/api/v1/documents") && method !== "GET") state.writes.push({ path, method, body: raw });
        function updateText(document: DocumentListItem, text: string) { state.texts.set(document.ref, text); document.text = { ...document.text, total_chars: Array.from(text).length, content_hash: `hash-${document.ref}-${document.revision}` }; }
        function field(name: string) { return new RegExp(`name="${name}"\\r?\\n\\r?\\n([^\\r\\n]*)`).exec(raw)?.[1] ?? ""; }
        function createUpload(ownerTaskId: number | null, filename: string, caption: string) {
            const ref = `opaque-upload-${state.documents.length}`;
            const document = documentFixture({ ref, source_kind: "upload", title: filename, format: filename.split(".").at(-1) ?? "txt", caption: caption || null, ownership: { kind: ownerTaskId ? "task" : "project", project_id: 1, owner_task_id: ownerTaskId }, tasks: ownerTaskId ? [{ id: ownerTaskId, key: "DEMO-1", title: base.tasks[0].title, relation: "owner" }] : [], original: { filename, content_type: "text/plain", size: 100, content_url: `/api/v1/documents/${ref}/content`, preview_url: null }, text: { ...plan.text, rendition: "extracted_text" }, capabilities: { ...plan.capabilities, can_link: ownerTaskId === null } });
            state.documents.push(document); updateText(document, "Согласованные расходы по проекту"); return document;
        }
        if (path === "/api/v1/documents/upload" && method === "POST") {
            const id = field("request_id"); if (receipts.has(id)) return reply(receipts.get(id));
            const filename = /filename="([^"]+)"/.exec(raw)?.[1] ?? "Документ.txt";
            state.uploads.push(filename);
            if (filename === "error.pdf") return reply({ detail: "Нет места для документа" }, 503);
            const document = createUpload(Number(field("owner_task_id")) || null, filename, field("caption")); receipts.set(id, document); return reply(document, 201);
        }
        if (path === "/api/v1/documents") {
            if (method === "POST") {
                const body = request.postDataJSON(); if (receipts.has(body.request_id)) return reply(receipts.get(body.request_id));
                const ref = `opaque-created-${state.documents.length}`;
                const document = documentFixture({ ref, title: body.title, ownership: { kind: body.owner_task_id ? "task" : "project", project_id: 1, owner_task_id: body.owner_task_id ?? null }, tasks: body.owner_task_id ? [{ id: body.owner_task_id, key: "DEMO-1", title: base.tasks[0].title, relation: "owner" }] : [], capabilities: { ...plan.capabilities, can_link: !body.owner_task_id } });
                state.documents.push(document); updateText(document, body.content_md); receipts.set(body.request_id, document); return reply(document, 201);
            }
            let items = state.documents.filter(document => !url.searchParams.has("task_id") || document.tasks.some(task => task.id === Number(url.searchParams.get("task_id"))));
            if (url.searchParams.get("scope") === "project") items = items.filter(document => !document.tasks.length);
            if (url.searchParams.get("scope") === "tasks") items = items.filter(document => !!document.tasks.length);
            for (const key of ["source_kind", "format"] as const) if (url.searchParams.get(key)) items = items.filter(document => document[key] === url.searchParams.get(key));
            const processing = url.searchParams.get("processing");
            if (processing) items = items.filter(document => processing === "failed" ? Object.values(document.states).some(state => state.status === "failed") : processing === "pending" ? Object.values(document.states).some(state => ["pending", "processing"].includes(state.status)) : !Object.values(document.states).some(state => ["pending", "processing", "failed"].includes(state.status)));
            const q = url.searchParams.get("q")?.toLocaleLowerCase("ru"); if (q) items = items.filter(document => `${document.title} ${document.caption ?? ""} ${state.texts.get(document.ref) ?? ""}`.toLocaleLowerCase("ru").includes(q));
            const offset = Number(url.searchParams.get("offset") ?? 0); const limit = Number(url.searchParams.get("limit") ?? 20);
            return reply(documentPageFixture(items.slice(offset, offset + limit), { total: items.length, offset, limit, next_offset: offset + limit < items.length ? offset + limit : null }));
        }
        const match = /^\/api\/v1\/documents\/([^/]+)(?:\/(.*))?$/.exec(path);
        if (match) {
            const document = state.documents.find(document => document.ref === decodeURIComponent(match[1])); if (!document) return reply({ detail: "Нет доступа" }, 404);
            const suffix = match[2] ?? "";
            if (suffix === "text") {
                const all = Array.from(state.texts.get(document.ref) ?? ""); const offset = Number(url.searchParams.get("offset") ?? 0); const limit = Number(url.searchParams.get("max_chars") ?? 8000);
                return reply(documentTextFixture({ ref: document.ref, text: all.slice(offset, offset + limit).join(""), offset, next_offset: offset + limit < all.length ? offset + limit : null, total_chars: all.length, content_hash: document.text.content_hash!, rendition: document.text.rendition }));
            }
            if (suffix === "preview" || suffix === "content") return route.fulfill({ headers, contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jV3sAAAAASUVORK5CYII=", "base64") });
            if (suffix === "retry") { document.states.extraction.status = "ready"; document.states.fts.status = "ready"; return reply(document); }
            if (suffix.startsWith("tasks/")) {
                const taskId = Number(suffix.split("/")[1]);
                if (method === "POST" && !document.tasks.some(task => task.id === taskId)) document.tasks.push({ id: taskId, key: "DEMO-1", title: base.tasks[0].title, relation: "linked" });
                if (method === "DELETE") document.tasks = document.tasks.filter(task => task.id !== taskId);
                document.capabilities.can_detach = !!document.tasks.length; return reply(document);
            }
            if (method === "DELETE") { state.documents = state.documents.filter(item => item.ref !== document.ref); return reply(null, 204); }
            if (method === "PATCH") { const body = request.postDataJSON(); if (body.expected_revision !== document.revision) return reply({ detail: "Версия документа изменилась" }, 409); document.revision += 1; Object.assign(document, { title: body.title, caption: body.caption }); if (body.content_md !== undefined) updateText(document, body.content_md); }
            return reply(document);
        }
        if (path === "/api/v1/tasks/100/comments") return reply(state.comments);
        if (path === "/api/v1/tasks/100/comments/with-files") {
            state.commentSubmissions.push(raw);
            if (state.failNextComments-- > 0) return reply({ detail: "Связь прервалась" }, 503);
            const refs: string[] = JSON.parse(field("document_refs") || "[]");
            const filename = /filename="([^"]+)"/.exec(raw)?.[1];
            if (filename) refs.push(createUpload(100, filename, JSON.parse(field("captions") || "[]")[0] ?? "").ref);
            const comment: TaskComment = { id: 11, task_id: 100, author: base.me, author_name: null, body_md: field("body_md"), document_refs: refs, created_at: plan.created_at };
            state.comments.push(comment); return reply(comment, 201);
        }
        if (path === "/api/v1/comments/11" && method === "DELETE") { state.comments = []; return reply(null, 204); }
        return route.fallback();
    });
    return state;
}
