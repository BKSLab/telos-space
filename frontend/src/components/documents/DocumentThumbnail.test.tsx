// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { apiUrl } from "@/lib/api";
import type { DocumentListItem } from "@/lib/types";
import { DocumentThumbnail } from "./DocumentThumbnail";
import { documentFixture } from "./documentFixtures.test-utils";

const create = vi.fn<(blob: Blob) => string>();
const revoke = vi.fn<(url: string) => void>();
const fetchPreview = vi.fn<typeof fetch>();
const jpeg = () => new Response("server thumbnail", { headers: { "Content-Type": "image/jpeg" } });
function imageDocument(ref = "image-one", patch: Partial<DocumentListItem> = {}) {
    return documentFixture({ ref, title: "Схема проекта", source_kind: "upload", format: "png",
        original: { filename: "Схема.png", content_type: "image/png", size: 10240, content_url: `/api/v1/documents/${ref}/content`, preview_url: `/api/v1/documents/${ref}/preview` }, ...patch });
}
function thumbnail(document: DocumentListItem) {
    return <DocumentThumbnail document={document} file={{ name: document.original?.filename ?? "document.md", mime: document.original?.content_type ?? "text/markdown" }} />;
}
beforeEach(() => {
    create.mockReset().mockImplementation(() => `blob:thumbnail-${create.mock.calls.length}`);
    revoke.mockReset();
    fetchPreview.mockReset().mockImplementation(async () => jpeg());
    vi.stubGlobal("fetch", fetchPreview);
    vi.stubGlobal("IntersectionObserver", undefined);
    vi.stubGlobal("URL", class extends URL { static createObjectURL = create; static revokeObjectURL = revoke; });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("загружает только защищённое JPEG-превью с cookie и освобождает URL после закрытия списка", async () => {
    const document = imageDocument();
    const { container, unmount } = render(thumbnail(document));
    await waitFor(() => expect(container.querySelector("img")?.getAttribute("src")).toBe("blob:thumbnail-1"));
    expect(fetchPreview).toHaveBeenCalledExactlyOnceWith(apiUrl(document.original!.preview_url!), { credentials: "include", signal: expect.any(AbortSignal), redirect: "error" });
    expect(container.querySelector("img")?.getAttribute("loading")).toBe("lazy");
    expect(container.querySelector("img")?.getAttribute("alt")).toBe("");
    expect(revoke).not.toHaveBeenCalled();
    const signal = fetchPreview.mock.calls[0][1]!.signal!;
    unmount();
    expect(signal.aborted).toBe(true);
    expect(revoke).toHaveBeenCalledExactlyOnceWith("blob:thumbnail-1");
});

it("откладывает HTTP до появления строки в области видимости и отключает observer", async () => {
    let intersect!: IntersectionObserverCallback;
    const observe = vi.fn(); const disconnect = vi.fn();
    vi.stubGlobal("IntersectionObserver", class {
        constructor(callback: IntersectionObserverCallback) { intersect = callback; }
        observe = observe;
        disconnect = disconnect;
    });
    const { container, unmount } = render(thumbnail(imageDocument()));
    expect(observe).toHaveBeenCalledWith(container.firstElementChild);
    expect(fetchPreview).not.toHaveBeenCalled();
    act(() => intersect([{ isIntersecting: false } as IntersectionObserverEntry], {} as IntersectionObserver));
    expect(fetchPreview).not.toHaveBeenCalled();
    act(() => intersect([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver));
    await waitFor(() => expect(container.querySelector("img")).toBeTruthy());
    act(() => intersect([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver));
    expect(fetchPreview).toHaveBeenCalledTimes(1);
    expect(disconnect).toHaveBeenCalled();
    unmount();
    expect(disconnect).toHaveBeenCalledTimes(2);
});

it.each([401, 403, 404, 500])("возвращает значок при HTTP %s без запроса оригинала", async status => {
    fetchPreview.mockResolvedValue(new Response(null, { status }));
    const { container } = render(thumbnail(imageDocument()));
    await act(async () => {});
    expect(fetchPreview).toHaveBeenCalledTimes(1);
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toBe("PNG");
    expect(create).not.toHaveBeenCalled();
});

it("сохраняет fallback при сетевой ошибке и не принимает активный формат вместо JPEG", async () => {
    fetchPreview.mockRejectedValueOnce(new TypeError("Failed to fetch"))
        .mockResolvedValueOnce(new Response("<svg />", { headers: { "Content-Type": "image/svg+xml" } }));
    const { container, rerender } = render(thumbnail(imageDocument()));
    await act(async () => {});
    expect(container.textContent).toBe("PNG");
    rerender(thumbnail(imageDocument("image-two")));
    await act(async () => {});
    expect(container.querySelector("img")).toBeNull();
    expect(create).not.toHaveBeenCalled();
});

it("после ошибки декодирования сразу освобождает миниатюру и сохраняет значок", async () => {
    const { container, unmount } = render(thumbnail(imageDocument()));
    await waitFor(() => expect(container.querySelector("img")).toBeTruthy());
    fireEvent.error(container.querySelector("img")!);
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toBe("PNG");
    expect(revoke).toHaveBeenCalledExactlyOnceWith("blob:thumbnail-1");
    unmount();
    expect(revoke).toHaveBeenCalledTimes(1);
});

it.each([null, "https://external.example/preview", "//external.example/preview", "/api/v1/documents/image-one/content", "/api/v1/documents/another-document/preview"])("не подменяет отсутствующее или неканоническое превью (%s) оригиналом", async preview_url => {
    const document = imageDocument();
    document.original!.preview_url = preview_url;
    const { container } = render(thumbnail(document));
    await act(async () => {});
    expect(fetchPreview).not.toHaveBeenCalled();
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toBe("PNG");
});

it("не запрашивает превью формата, который не является изображением", async () => {
    const document = imageDocument();
    document.original!.filename = "План.pdf";
    document.original!.content_type = "application/pdf";
    const { container } = render(thumbnail(document));
    await act(async () => {});
    expect(fetchPreview).not.toHaveBeenCalled();
    expect(container.textContent).toBe("PDF");
});

it("сразу скрывает прежнюю миниатюру при смене документа и освобождает её до новой загрузки", async () => {
    let resolveNext!: (response: Response) => void;
    fetchPreview.mockImplementationOnce(async () => jpeg()).mockImplementationOnce(() => new Promise(resolve => { resolveNext = resolve; }));
    const { container, rerender } = render(thumbnail(imageDocument()));
    await waitFor(() => expect(container.querySelector("img")?.getAttribute("src")).toBe("blob:thumbnail-1"));
    rerender(thumbnail(imageDocument("image-two")));
    expect(container.querySelector("img")).toBeNull();
    expect(revoke).toHaveBeenCalledExactlyOnceWith("blob:thumbnail-1");
    await act(async () => { resolveNext(jpeg()); });
    expect(container.querySelector("img")?.getAttribute("src")).toBe("blob:thumbnail-2");
});

it("отменяет прежний запрос и игнорирует его поздний ответ после смены документа", async () => {
    let resolvePrevious!: (response: Response) => void;
    fetchPreview.mockImplementationOnce(() => new Promise(resolve => { resolvePrevious = resolve; }));
    const { container, rerender } = render(thumbnail(imageDocument()));
    const signal = fetchPreview.mock.calls[0][1]!.signal!;
    rerender(thumbnail(imageDocument("image-two")));
    expect(signal.aborted).toBe(true);
    await waitFor(() => expect(container.querySelector("img")?.getAttribute("src")).toBe("blob:thumbnail-1"));
    await act(async () => { resolvePrevious(jpeg()); });
    expect(create).toHaveBeenCalledTimes(1);
    expect(container.querySelector("img")?.getAttribute("src")).toBe("blob:thumbnail-1");
});

it("отменяет загрузку при размонтировании, даже если ответ уже получен, а Blob ещё читается", async () => {
    let resolveBlob!: (blob: Blob) => void;
    const response = jpeg();
    vi.spyOn(response, "blob").mockImplementation(() => new Promise(resolve => { resolveBlob = resolve; }));
    fetchPreview.mockResolvedValue(response);
    const { unmount } = render(thumbnail(imageDocument()));
    await waitFor(() => expect(response.blob).toHaveBeenCalled());
    const signal = fetchPreview.mock.calls[0][1]!.signal!;
    unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => { resolveBlob(new Blob(["thumbnail"], { type: "image/jpeg" })); });
    expect(create).not.toHaveBeenCalled();
});

it("после отзыва чтения скрывает и освобождает уже загруженное превью без повторного запроса", async () => {
    const document = imageDocument();
    const { container, rerender } = render(thumbnail(document));
    await waitFor(() => expect(container.querySelector("img")).toBeTruthy());
    rerender(thumbnail({ ...document, capabilities: { ...document.capabilities, can_read: false } }));
    expect(container.querySelector("img")).toBeNull();
    expect(revoke).toHaveBeenCalledExactlyOnceWith("blob:thumbnail-1");
    expect(fetchPreview).toHaveBeenCalledTimes(1);
});

it("при новой версии того же документа не оставляет прежнее изображение", async () => {
    const document = imageDocument();
    const { container, rerender } = render(thumbnail(document));
    await waitFor(() => expect(container.querySelector("img")).toBeTruthy());
    rerender(thumbnail({ ...document, revision: 2 }));
    expect(container.querySelector("img")).toBeNull();
    expect(revoke).toHaveBeenCalledExactlyOnceWith("blob:thumbnail-1");
    await waitFor(() => expect(container.querySelector("img")?.getAttribute("src")).toBe("blob:thumbnail-2"));
});
