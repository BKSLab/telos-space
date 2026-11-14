import type { DocumentFragment, DocumentListItem, DocumentTextPage } from "./types";
import { api, apiUrl, endpoints } from "./api";
import { newUuid } from "./uuid";

export const DOCUMENT_PAGE_SIZE = 20;
export const DOCUMENT_TEXT_PAGE_SIZE = 8000;
export const DOCUMENT_UPLOAD_ACCEPT = [".avif", ".bmp", ".csv", ".docx", ".gif", ".jpeg", ".jpg", ".log", ".md", ".pdf", ".png", ".txt", ".webp", ".xls", ".xlsm", ".xlsx"].join(",");
export const MAX_DOCUMENT_BATCH = 10;
export const MAX_DOCUMENT_SIZE = 10 * 1024 * 1024;
export const MAX_DOCUMENT_ORIGINAL_PREVIEW_SIZE = 512 * 1024;
const PROCESSING_STATES = new Set(["pending", "processing", "unknown", "stale"]);
export type DocumentOwner = { projectId: number; taskId?: never } | { taskId: number; projectId?: never };
export interface MarkdownDocumentDraft { key: string; title: string; content_md: string }
export interface DocumentFilters {
    project_id?: number;
    task_id?: number;
    q?: string;
    scope?: "all" | "project" | "tasks";
    source_kind?: "text" | "upload";
    format?: string;
    processing?: string;
    limit?: number;
    offset?: number;
}

export function documentQuery(filters: DocumentFilters): string {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
        if (value !== undefined && value !== "") params.set(key, String(value));
    }
    return params.toString();
}

export function documentProcessingLabel(document: DocumentListItem): string | null {
    const extraction = document.states.extraction.status;
    if (extraction === "pending" || extraction === "processing") return "Извлекаем текст";
    if (extraction === "failed") return "Ошибка извлечения";
    if (Object.values(document.states).some(state => state.status === "failed")) return "Ошибка обработки";
    if (Object.values(document.states).some(state => PROCESSING_STATES.has(state.status))) return "Обрабатывается";
    if (["empty", "unsupported", "disabled"].includes(extraction)) return "Поиск по названию и подписи";
    return null;
}

export function documentsPollInterval(items: DocumentListItem[] | undefined, failed: boolean): number | false {
    return !failed && items?.some(document => Object.values(document.states).some(state => PROCESSING_STATES.has(state.status))) ? 2000 : false;
}

export function createMarkdownDocument(owner: DocumentOwner, draft: Pick<MarkdownDocumentDraft, "title" | "content_md">, requestId = newUuid()) {
    return api.post<DocumentListItem>(endpoints.documents(), {
        request_id: requestId,
        title: draft.title.trim(),
        content_md: draft.content_md,
        ...(owner.taskId !== undefined ? { owner_task_id: owner.taskId } : { project_id: owner.projectId }),
    });
}

export function uploadDocument(owner: DocumentOwner, file: File, caption: string, requestId = newUuid()) {
    const body = new FormData();
    body.append("request_id", requestId);
    body.append("file", file);
    body.append(owner.taskId !== undefined ? "owner_task_id" : "project_id", String(owner.taskId ?? owner.projectId));
    if (caption.trim()) body.append("caption", caption.trim());
    return api.postForm<DocumentListItem>(endpoints.documentUpload(), body);
}

export async function readDocumentOriginal(url: string, signal: AbortSignal): Promise<string> {
    const response = await fetch(apiUrl(url), { credentials: "include", signal });
    if (!response.ok) throw new Error("Не удалось прочитать оригинал документа.");
    if (Number(response.headers.get("content-length")) > MAX_DOCUMENT_ORIGINAL_PREVIEW_SIZE) throw new Error("Оригинал слишком велик для встроенного просмотра. Скачайте документ.");
    const reader = response.body?.getReader();
    const parts: Uint8Array[] = []; let size = 0;
    if (reader) {
        while (true) {
            const { value, done } = await reader.read(); if (done) break;
            size += value.byteLength;
            if (size > MAX_DOCUMENT_ORIGINAL_PREVIEW_SIZE) { await reader.cancel(); throw new Error("Оригинал слишком велик для встроенного просмотра. Скачайте документ."); }
            parts.push(value);
        }
    } else {
        const value = new Uint8Array(await response.arrayBuffer());
        if (value.byteLength > MAX_DOCUMENT_ORIGINAL_PREVIEW_SIZE) throw new Error("Оригинал слишком велик для встроенного просмотра. Скачайте документ.");
        parts.push(value); size = value.byteLength;
    }
    const bytes = new Uint8Array(size); let offset = 0;
    for (const part of parts) { bytes.set(part, offset); offset += part.byteLength; }
    const encoding = bytes[0] === 0xff && bytes[1] === 0xfe ? "utf-16le" : bytes[0] === 0xfe && bytes[1] === 0xff ? "utf-16be" : "utf-8";
    return new TextDecoder(encoding, { fatal: true }).decode(bytes);
}

/** Offsets refer to Unicode code points, as in the server's raw rendition. */
export function documentFragmentParts(page: DocumentTextPage, fragment: DocumentFragment): { before: string; match: string; after: string } | null {
    if (page.ref !== fragment.document_ref || page.rendition !== fragment.rendition || page.content_hash !== fragment.content_hash
        || page.offset_unit !== "unicode_codepoints" || fragment.offset_unit !== "unicode_codepoints"
        || !Number.isSafeInteger(fragment.start) || !Number.isSafeInteger(fragment.end)
        || fragment.start < page.offset || fragment.end <= fragment.start || fragment.end > page.total_chars) return null;
    const points = Array.from(page.text);
    const start = fragment.start - page.offset;
    const end = fragment.end - page.offset;
    if (end > points.length) return null;
    const match = points.slice(start, end).join("");
    if (match !== fragment.quote) return null;
    return { before: points.slice(0, start).join(""), match, after: points.slice(end).join("") };
}
