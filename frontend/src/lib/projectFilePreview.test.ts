import { afterEach, expect, it, vi } from "vitest";
import { decodeOriginalText, MAX_ORIGINAL_TEXT_PREVIEW_BYTES, ORIGINAL_TEXT_TOO_LARGE, originalTextFormat, readOriginalText } from "./projectFilePreview";

afterEach(() => vi.restoreAllMocks());

it("читает Unicode, BOM UTF-16 и Windows-1251 без изменения строк", () => {
    expect(decodeOriginalText(new TextEncoder().encode("Заголовок 🛰️\n\nСтрока 2"))).toBe("Заголовок 🛰️\n\nСтрока 2");
    expect(decodeOriginalText(new Uint8Array([0xff, 0xfe, 0x1f, 0x04, 0x40, 0x04]))).toBe("Пр");
    expect(decodeOriginalText(new Uint8Array([0xcf, 0xf0, 0xe8, 0xe2, 0xe5, 0xf2]))).toBe("Привет");
    expect(["TXT", "log", "csv", "md", "html", "pdf"].map(extension => originalTextFormat({ original_name: `file.${extension}` })))
        .toEqual(["text", "text", "text", "markdown", null, null]);
});

it("читает защищённый оригинал с cookie и сигналом отмены", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("# План\n\nТекст 🪐"));
    const controller = new AbortController();
    expect(await readOriginalText(1, 5, controller.signal)).toBe("# План\n\nТекст 🪐");
    expect(fetch).toHaveBeenCalledWith(expect.stringMatching(/\/projects\/1\/files\/5\/content$/), {
        credentials: "include", signal: controller.signal, cache: "no-store",
    });
});

it("отказывает в слишком большом оригинале по заголовку и потоку без скрытого обрезания", async () => {
    const cancel = vi.fn();
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(new ReadableStream({ cancel }), {
        headers: { "content-length": String(MAX_ORIGINAL_TEXT_PREVIEW_BYTES + 1) },
    }));
    await expect(readOriginalText(1, 5, new AbortController().signal)).rejects.toThrow(ORIGINAL_TEXT_TOO_LARGE);
    expect(cancel).toHaveBeenCalledOnce();
    fetch.mockResolvedValueOnce(new Response(new Uint8Array(MAX_ORIGINAL_TEXT_PREVIEW_BYTES + 1)));
    await expect(readOriginalText(1, 5, new AbortController().signal)).rejects.toThrow(ORIGINAL_TEXT_TOO_LARGE);
});
