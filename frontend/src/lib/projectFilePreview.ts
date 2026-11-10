import { apiUrl, ApiError, endpoints } from "./api";
import { fileExtension } from "./files";
import type { ProjectFile } from "./types";

// Большое Markdown-дерево может заблокировать браузер даже у допустимой загрузки.
export const MAX_ORIGINAL_TEXT_PREVIEW_BYTES = 1024 * 1024;
export const ORIGINAL_TEXT_TOO_LARGE = "Файл больше 1 МБ — скачайте оригинал для полного просмотра. Извлечённый текст можно читать по страницам во второй вкладке.";

export function originalTextFormat(file: Pick<ProjectFile, "original_name">): "markdown" | "text" | null {
    const extension = fileExtension(file.original_name);
    if (extension === "md") return "markdown";
    return ["txt", "log", "csv"].includes(extension) ? "text" : null;
}

export function decodeOriginalText(bytes: Uint8Array): string {
    if (bytes[0] === 0xff && bytes[1] === 0xfe) return new TextDecoder("utf-16le").decode(bytes);
    if (bytes[0] === 0xfe && bytes[1] === 0xff) return new TextDecoder("utf-16be").decode(bytes);
    try {
        return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch {
        return new TextDecoder("windows-1251").decode(bytes);
    }
}

/** Читает защищённый оригинал без запуска извлечения и скрытого обрезания. */
export async function readOriginalText(projectId: number, fileId: number, signal: AbortSignal): Promise<string> {
    const response = await fetch(apiUrl(endpoints.projectFileContent(projectId, fileId)), {
        credentials: "include", signal, cache: "no-store",
    });
    if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new ApiError(response.status, typeof body?.detail === "string" ? body.detail : "Не удалось загрузить исходный файл.");
    }
    if (Number(response.headers.get("content-length")) > MAX_ORIGINAL_TEXT_PREVIEW_BYTES) {
        await response.body?.cancel();
        throw new Error(ORIGINAL_TEXT_TOO_LARGE);
    }
    const reader = response.body?.getReader();
    if (!reader) throw new Error("Не удалось прочитать исходный файл.");
    const parts: Uint8Array[] = [];
    let size = 0;
    try {
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            size += value.byteLength;
            if (size > MAX_ORIGINAL_TEXT_PREVIEW_BYTES) {
                await reader.cancel();
                throw new Error(ORIGINAL_TEXT_TOO_LARGE);
            }
            parts.push(value);
        }
    } finally {
        reader.releaseLock();
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const part of parts) {
        bytes.set(part, offset);
        offset += part.byteLength;
    }
    return decodeOriginalText(bytes);
}
