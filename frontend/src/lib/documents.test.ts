import { describe, expect, it } from "vitest";
import { documentFragmentParts, documentQuery, documentsPollInterval, readDocumentOriginal, MAX_DOCUMENT_ORIGINAL_PREVIEW_SIZE } from "./documents";
import { documentFixture, documentTextFixture } from "@/components/documents/documentFixtures.test-utils";
import { vi } from "vitest";
import type { DocumentFragment } from "./types";

const page = documentTextFixture({ text: "А🧭Б В", total_chars: 5 });
const fragment: DocumentFragment = { document_ref: page.ref, rendition: page.rendition, content_hash: page.content_hash, start: 1, end: 3, quote: "🧭Б", offset_unit: "unicode_codepoints" };
describe("ссылка на фрагмент документа", () => {
    it("считает смещения в Unicode code points", () => { expect(documentFragmentParts(page, fragment)).toEqual({ before: "А", match: "🧭Б", after: " В" }); });
    it.each([
        { content_hash: "old-hash" }, { document_ref: "other-document" }, { rendition: "extracted_text" as const },
        { start: -1 }, { end: 9 }, { quote: "Несовпадающий текст" }, { start: 1.5 }, { end: 1 },
    ])("отклоняет устаревшую или неверную ссылку %j", patch => { expect(documentFragmentParts(page, { ...fragment, ...patch })).toBeNull(); });
    it("проверяет диапазон внутри страницы со смещением", () => {
        expect(documentFragmentParts({ ...page, text: "🧭Б В", offset: 1 }, fragment)?.match).toBe("🧭Б");
        expect(documentFragmentParts({ ...page, text: "Б В", offset: 2 }, fragment)).toBeNull();
    });
});
it("отправляет фильтры без q, не добавляет пустые значения и кодирует запрос", () => {
    const params = new URLSearchParams(documentQuery({ project_id: 1, task_id: 42, q: "", format: "pdf", processing: "failed", limit: 20, offset: 0 }));
    expect(params.has("q")).toBe(false);
    expect(params.get("task_id")).toBe("42");
    expect(params.get("processing")).toBe("failed");
    expect(new URLSearchParams(documentQuery({ q: "бюджет & план" })).get("q")).toBe("бюджет & план");
});
it("обновляет незавершённые стадии, останавливается после готовности и ошибки чтения", () => {
    const ready = documentFixture();
    const stale = documentFixture({ states: { ...ready.states, semantic: { status: "stale", detail: null } } });
    expect(documentsPollInterval([ready], false)).toBe(false);
    expect(documentsPollInterval([stale], false)).toBe(2000);
    expect(documentsPollInterval([stale], true)).toBe(false);
});
it("читает оригинал с cookie и ограничивает поток до помещения в DOM", async () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    try {
        fetch.mockResolvedValueOnce(new Response("Текст 🧭"));
        expect(await readDocumentOriginal("/api/v1/documents/ref/content", new AbortController().signal)).toBe("Текст 🧭");
        expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/documents/ref/content"), expect.objectContaining({ credentials: "include" }));
        fetch.mockResolvedValueOnce(new Response(new Uint8Array(MAX_DOCUMENT_ORIGINAL_PREVIEW_SIZE + 1)));
        await expect(readDocumentOriginal("/api/v1/documents/ref/content", new AbortController().signal)).rejects.toThrow("слишком велик");
    } finally { fetch.mockRestore(); }
});
