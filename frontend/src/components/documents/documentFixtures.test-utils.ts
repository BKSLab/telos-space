import type { DocumentListItem, DocumentPage, DocumentTextPage } from "@/lib/types";

export const documentStamp = "2026-11-14T10:00:00Z";
export function documentFixture(patch: Partial<DocumentListItem> = {}): DocumentListItem {
    return {
        ref: "test-opaque-document", source_kind: "text", title: "План проекта", caption: null,
        author: { id: 1, name: "Иван" }, ownership: { kind: "project", project_id: 1, owner_task_id: null }, tasks: [],
        created_at: documentStamp, updated_at: documentStamp, revision: 1, format: "md",
        states: { extraction: { status: "not_required", detail: null }, fts: { status: "ready", detail: null }, semantic: { status: "disabled", detail: null } },
        original: null,
        text: { available: true, total_chars: 11, url: "/api/v1/documents/test-opaque-document/text", rendition: "markdown", content_hash: "current-hash" },
        capabilities: { can_read: true, can_edit: true, can_delete: true, can_retry: false, can_link: true, can_detach: false },
        ...patch,
    };
}
export function documentPageFixture(items: DocumentListItem[], patch: Partial<DocumentPage> = {}): DocumentPage {
    return { items, total: items.length, limit: 20, offset: 0, next_offset: null, ...patch };
}
export function documentTextFixture(patch: Partial<DocumentTextPage> = {}): DocumentTextPage {
    const text = "# План\n\nРабота 🧭 по проекту";
    return { ref: "test-opaque-document", text, rendition: "markdown", content_hash: "current-hash", offset: 0, next_offset: null, total_chars: Array.from(text).length, offset_unit: "unicode_codepoints", ...patch };
}
