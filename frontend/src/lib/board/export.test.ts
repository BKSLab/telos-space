// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { escapeXML, exportPNG, exportSVG, LIGHT_EXPORT_PALETTE, PNG_LIMITS, pngDimensions, resolveExportPalette } from "@/lib/board/export";
import { boundsOfPoints } from "@/lib/board/geometry";
import { arrowHeadPolygon } from "@/lib/board/routing";
import { connectorPoints, nodeLookup, stickerObject, type Scene, type SceneObject, type ShapeObject } from "@/lib/board/scene";
import type { BoardSticker } from "@/lib/board/stickers";
import { defaultStyle, type ShapeKind } from "@/lib/board/types";

function shape(key: string, kind: ShapeKind = "rect", patch: Partial<ShapeObject> = {}): ShapeObject {
    return { key, type: "shape", kind, x: 0, y: 0, w: 100, h: 80, z: 0, points: null, startRef: null, endRef: null, style: defaultStyle(kind), label: "", ...patch };
}
function scene(...objects: SceneObject[]): Scene { return new Map(objects.map((object) => [object.key, object])); }
function parse(svg: string): Document {
    const xml = new DOMParser().parseFromString(svg, "image/svg+xml");
    expect(xml.querySelector("parsererror")).toBeNull();
    return xml;
}
function note(patch: Partial<BoardSticker> = {}): BoardSticker {
    return {
        id: 7, project_id: 1, body: "Тело", color: "#fff19a", canvas_x: 0, canvas_y: 0,
        created_by_user_id: null, created_by_username_snapshot: "", created_by_display_name_snapshot: "",
        task_ids: [], revision: 1, created_at: "", updated_at: "", ...patch,
    };
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("SVG всей сцены и выделения", () => {
    it("экспортирует объекты вне viewport, отрицательные координаты и общие слои без DOM", () => {
        const near = shape("n1", "rect", { z: 1 });
        const far = shape("n2", "ellipse", { x: 500_000, y: -900_000, z: -2 });
        const result = exportSVG(scene(near, far), { padding: 10 });
        const xml = parse(result.svg);
        expect([...xml.querySelectorAll("g[data-key]")].map((g) => g.getAttribute("data-key"))).toEqual(["n2", "n1"]);
        expect(result.objectCount).toBe(2);
        expect(result.bounds.x).toBe(-11);
        expect(result.bounds.y).toBe(-900_011);
        expect(result.bounds.x + result.bounds.w).toBe(500_111);
        expect(result.blob.type).toBe("image/svg+xml;charset=utf-8");
        expect(result.svg).not.toMatch(/foreignObject|<style|<script|<image|href=|var\(/);
    });

    it("пустое/несуществующее выделение не подменяет всей доской, generator работает", () => {
        const all = scene(shape("a"), shape("b", "text", { x: 2000 }));
        expect(parse(exportSVG(all, { selectedKeys: new Set(["b"]) }).svg).querySelectorAll("g[data-key]")).toHaveLength(1);
        expect(exportSVG(all, { selectedKeys: (function* () { yield "b"; })() }).bounds.x).toBeGreaterThan(1900);
        expect(() => exportSVG(all, { selectedKeys: [] })).toThrow("Выделите");
        expect(() => exportSVG(all, { selectedKeys: ["missing"] })).toThrow("Выделите");
        expect(() => exportSVG(new Map())).toThrow("нет объектов");
    });

    it("не допускает XML/attribute injection и заменяет запрещённые XML codepoints", () => {
        const attack = '<script>alert("x")</script> & \' <image href="https://evil"/>\u0000\ud800';
        const key = '"><script>attack</script><g onload="bad';
        const result = exportSVG(scene(shape(key, "text", { label: attack, w: 2000, h: 200 })));
        const xml = parse(result.svg);
        expect(xml.querySelector("g")?.getAttribute("data-key")).toBe(key);
        expect(xml.querySelector("text")?.textContent).toBe(attack.replace("\u0000", "�").replace("\ud800", "�"));
        expect(xml.querySelectorAll("script,image,[onload]")).toHaveLength(0);
        expect(escapeXML('&<>"\'\u0001')).toBe("&amp;&lt;&gt;&quot;&apos;�");
    });

    it.each(["url(https://evil)", 'red" onload="alert(1)', "var(--bad)", "currentColor", "rgb(999, 0, 0)", "rgba(0,0,0,2)", "#ffffff;stroke:red"])("отклоняет небезопасный цвет %s", (color) => {
        expect(() => exportSVG(scene(shape("a")), { background: color })).toThrow("Недопустимый цвет");
        const palette = { ...LIGHT_EXPORT_PALETTE, text: color };
        expect(() => exportSVG(scene(shape("a", "text", { label: "x" })), { palette })).toThrow("Недопустимый цвет");
    });

    it("все шесть видов, толщина/dash/font/alignment/fill/stroke и замена палитры темы", () => {
        const kinds: ShapeKind[] = ["rect", "ellipse", "diamond", "text", "line", "arrow"];
        const all = scene(...kinds.map((kind, index) => shape(kind, kind, {
            x: index * 200, label: kind, style: defaultStyle(kind, {
                stroke: "red", fill: "blue", stroke_width: 4, dash: "dashed", font_size: "l", text_align: "left", start_head: "arrow", end_head: "arrow",
            }),
        })));
        const palette = { ...LIGHT_EXPORT_PALETTE, stroke: { ...LIGHT_EXPORT_PALETTE.stroke, red: "#ff9393" }, fill: { ...LIGHT_EXPORT_PALETTE.fill, blue: "rgba(28, 47, 58, 0.8)" }, text: "#f4f4f5" };
        const xml = parse(exportSVG(all, { palette }).svg);
        expect(xml.querySelectorAll("g[data-key]")).toHaveLength(6);
        expect(xml.querySelector('[data-key="ellipse"] path')?.getAttribute("d")).toContain(" A ");
        expect(xml.querySelector('[data-key="diamond"] path')?.getAttribute("d")).toContain("L 98 40");
        expect(xml.querySelector('[data-key="rect"] path')?.getAttribute("fill")).toBe("rgba(28, 47, 58, 0.8)");
        expect(xml.querySelector('[data-key="arrow"]')?.querySelectorAll("path")).toHaveLength(3);
        expect(xml.querySelector('[data-key="rect"] path')?.getAttribute("stroke")).toBe("#ff9393");
        expect(xml.querySelector('[data-key="rect"] path')?.getAttribute("stroke-dasharray")).toBe("16 12");
        expect(xml.querySelector('[data-key="rect"] text')?.getAttribute("font-size")).toBe("18");
        expect(xml.querySelector('[data-key="rect"] text')?.getAttribute("text-anchor")).toBe("start");
    });

    it("стрелки используют живые refs к невыделенным фигурам/стикерам и orthogonal route", () => {
        const sticker = stickerObject(note({ canvas_x: 400, canvas_y: 300, width: 200, height: 160 }));
        const node = shape("node", "diamond", { x: 800, y: 100 });
        const arrow = shape("arrow", "arrow", {
            points: [[0, 0], [1, 1]], startRef: { type: "sticker", key: sticker.key, anchor: "right" },
            endRef: { type: "shape", key: "node", anchor: "left" }, style: defaultStyle("arrow", { route: "orthogonal" }),
        });
        const all = scene(sticker, node, arrow);
        const before = JSON.stringify([...all]);
        const result = exportSVG(all, { selectedKeys: ["arrow"], padding: 0 });
        const xml = parse(result.svg);
        const path = xml.querySelector("g path")!.getAttribute("d")!;
        expect(path).toContain("M 600 380");
        expect(path).toContain("L 800 140");
        expect((path.match(/L /g) ?? []).length).toBeGreaterThan(1);
        expect(xml.querySelectorAll("g[data-key]")).toHaveLength(1);
        expect(JSON.stringify([...all])).toBe(before);
        const points = connectorPoints(arrow, nodeLookup(all));
        const head = boundsOfPoints(arrowHeadPolygon(points.at(-2)!, points.at(-1)!, 2));
        expect(result.bounds.x).toBeLessThanOrEqual(head.x - 0.5);
        expect(result.bounds.y + result.bounds.h).toBeGreaterThanOrEqual(head.y + head.h + 0.5);
    });

    it("пропавшая ref сохраняет свободный endpoint; null points берут геометрию объекта", () => {
        const arrow = shape("a", "arrow", { x: -50, y: 10, w: 100, h: 20, endRef: { type: "shape", key: "missing", anchor: "left" } });
        const xml = parse(exportSVG(scene(arrow)).svg);
        expect(xml.querySelector("g path")?.getAttribute("d")).toBe("M -50 10 L 50 30");
    });

    it("bounds покрывают оба наконечника, толстые линии и подпись длиннее линии", () => {
        const arrow = shape("a", "arrow", { points: [[0, 0], [1, 0]], label: "длинная подпись ".repeat(20), style: defaultStyle("arrow", { start_head: "arrow", stroke_width: 4 }) });
        const result = exportSVG(scene(arrow), { padding: 0 });
        const xml = parse(result.svg);
        const rect = xml.querySelector("g > rect")!;
        expect(result.bounds.w).toBeGreaterThan(200);
        expect(result.bounds.h).toBeGreaterThan(100);
        expect(Number(rect.getAttribute("x"))).toBeGreaterThanOrEqual(result.bounds.x);
        expect(Number(rect.getAttribute("y")) + Number(rect.getAttribute("height"))).toBeLessThanOrEqual(result.bounds.y + result.bounds.h);
    });

    it("сохраняет переносы, переносит длинные слова и клиппит текст внутри рамки", () => {
        const result = exportSVG(scene(shape("a", "text", { label: "первая\n" + "я".repeat(1000), w: 100, h: 80 })));
        const xml = parse(result.svg);
        const text = xml.querySelector("text")!;
        const spans = text.querySelectorAll("tspan");
        expect(spans.length).toBeGreaterThan(1);
        expect(spans[0].textContent).toBe("первая");
        expect(spans[spans.length - 1].textContent).toContain("…");
        expect(text.getAttribute("clip-path")).toBe("url(#be-0-label)");
        for (const span of spans) expect(Number(span.getAttribute("textLength"))).toBeLessThanOrEqual(80);
    });

    it("экспортирует стикер с title/body/status и безопасным fallback цвета", () => {
        const sticker = stickerObject(note({ body: "<body>&", color: 'url(https://evil)"' }));
        Object.assign(sticker.sticker, { title: "Заголовок", status: "В работе" });
        const xml = parse(exportSVG(scene(sticker), { stickerText: () => ({ status: "Готово" }) }).svg);
        expect([...xml.querySelectorAll("text")].map((text) => text.textContent)).toEqual(["Заголовок", "<body>&", "Готово"]);
        expect(xml.querySelector("g > rect")?.getAttribute("fill")).toBe("#fff19a");
        expect(() => exportSVG(scene(sticker), { palette: { ...LIGHT_EXPORT_PALETTE, stickerFill: () => "url(https://evil)" } })).toThrow("Недопустимый цвет");
    });

    it.each([NaN, Infinity, -Infinity])("останавливает экспорт при нечисловой геометрии %s", (x) => {
        expect(() => exportSVG(scene(shape("bad", "rect", { x })))).toThrow("Недопустимое значение");
    });

    it("снимает actual CSS цвета темы, не оставляя probe в DOM", () => {
        const root = document.createElement("div");
        document.body.append(root);
        const colors = ["neutral", "yellow", "blue", "green", "red", "violet"];
        for (const color of colors) {
            root.style.setProperty("--board-stroke-" + color, "#123456");
            root.style.setProperty("--board-fill-" + color, "#abcdef");
        }
        const computed = vi.spyOn(window, "getComputedStyle").mockImplementation((element) => {
            const value = (element as HTMLElement).style.color;
            const color = value.startsWith("var(--board-stroke") ? "rgb(18, 52, 86)" : "rgb(171, 205, 239)";
            return { color } as CSSStyleDeclaration;
        });
        const palette = resolveExportPalette(root);
        expect(palette.stroke.red).toBe("rgb(18, 52, 86)");
        expect(palette.fill.blue).toBe("rgb(171, 205, 239)");
        expect(root.children).toHaveLength(0);
        expect(computed).toHaveBeenCalled();
        root.remove();
    });
});

describe("безопасные размеры PNG", () => {
    it("уменьшает с сохранением пропорций до dimension/pixel budget", () => {
        const size = pngDimensions(30_000, 20_000);
        expect(size.width).toBeLessThanOrEqual(PNG_LIMITS.maxDimension);
        expect(size.width * size.height).toBeLessThanOrEqual(PNG_LIMITS.maxPixels);
        expect(size.width / size.height).toBeCloseTo(1.5, 2);
        expect(size.scale).toBeLessThan(2);
        expect(pngDimensions(100, 50, { scale: 1 })).toEqual({ width: 100, height: 50, scale: 1 });
    });
    it("однопиксельная короткая сторона тоже не превышает бюджет", () => {
        expect(pngDimensions(1_000_000, 1, { maxPixels: 2, maxDimension: 8192 })).toMatchObject({ width: 2, height: 1 });
    });
    it.each([{ scale: NaN }, { scale: 0 }, { maxPixels: Infinity }, { maxPixels: 0 }, { maxDimension: 8193 }, { maxPixels: PNG_LIMITS.maxPixels + 1 }])("отклоняет обход лимитов %s", (options) => {
        expect(() => pngDimensions(100, 100, options)).toThrow("Недопустимое");
    });
    it("можно отказать вместо уменьшения", () => {
        expect(() => pngDimensions(10_000, 10_000, { overflow: "error" })).toThrow("слишком большая");
    });
});

function pngBrowser(mode: "ok" | "load-error" | "null-context" | "null-blob" | "throw-blob" | "throw-draw" | "hang-load" | "hang-blob" = "ok") {
    const draw = vi.fn(() => { if (mode === "throw-draw") throw new Error("draw failed"); });
    const canvases: HTMLCanvasElement[] = [];
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(function (this: HTMLCanvasElement) {
        canvases.push(this);
        return mode === "null-context" ? null : { drawImage: draw } as unknown as CanvasRenderingContext2D;
    });
    vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation((callback) => {
        if (mode === "throw-blob") throw new Error("encoding failed");
        if (mode !== "hang-blob") callback(mode === "null-blob" ? null : new Blob(["png"], { type: "image/png" }));
    });
    class FakeImage {
        onload: (() => void) | null = null;
        onerror: (() => void) | null = null;
        value = "";
        set src(value: string) {
            this.value = value;
            if (value && mode !== "hang-load") queueMicrotask(() => mode === "load-error" ? this.onerror?.() : this.onload?.());
        }
        get src() { return this.value; }
    }
    vi.stubGlobal("Image", FakeImage);
    const urls: Blob[] = [];
    const create = vi.fn((blob: Blob) => { urls.push(blob); return "blob:board"; });
    const revoke = vi.fn();
    vi.stubGlobal("URL", { createObjectURL: create, revokeObjectURL: revoke });
    return { canvases, create, revoke, draw, urls };
}

describe("PNG lifecycle и ошибки браузера", () => {
    it("создаёт PNG, ограничивает intrinsic SVG до Image.onload и освобождает ресурсы", async () => {
        const browser = pngBrowser();
        const all = scene(shape("a"), shape("far", "rect", { x: 900_000 }));
        const result = await exportPNG(all, { maxDimension: 100, maxPixels: 5000 });
        expect(result.blob.type).toBe("image/png");
        expect(result.width).toBeLessThanOrEqual(100);
        expect(result.width * result.height).toBeLessThanOrEqual(5000);
        const source = await new Promise<string>((resolve) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result as string);
            reader.readAsText(browser.urls[0]);
        });
        const xml = parse(source);
        expect(xml.documentElement.getAttribute("width")).toBe(String(result.width));
        expect(xml.documentElement.getAttribute("height")).toBe(String(result.height));
        expect(xml.documentElement.getAttribute("viewBox")).toContain("900");
        expect(browser.draw).toHaveBeenCalledWith(expect.anything(), 0, 0, result.width, result.height);
        expect(browser.revoke).toHaveBeenCalledWith("blob:board");
        expect(browser.canvases[0].width).toBe(0);
        expect(browser.canvases[0].height).toBe(0);
    });

    it.each(["load-error", "null-blob", "throw-blob", "throw-draw"] as const)("обрабатывает %s и освобождает URL/canvas", async (mode) => {
        const browser = pngBrowser(mode);
        await expect(exportPNG(scene(shape("a")))).rejects.toThrow(/Не удалось|Браузер/);
        expect(browser.revoke).toHaveBeenCalledOnce();
        expect(browser.canvases[0].width).toBe(0);
    });

    it("null context не оставляет URL или canvas", async () => {
        const browser = pngBrowser("null-context");
        await expect(exportPNG(scene(shape("a")))).rejects.toThrow("создать изображение");
        expect(browser.create).not.toHaveBeenCalled();
        expect(browser.canvases[0].width).toBe(0);
    });

    it.each(["hang-load", "hang-blob"] as const)("таймаут при %s очищает URL", async (mode) => {
        vi.useFakeTimers();
        const browser = pngBrowser(mode);
        const pending = exportPNG(scene(shape("a")), { timeoutMs: 10 });
        const assertion = expect(pending).rejects.toThrow("слишком много времени");
        await vi.advanceTimersByTimeAsync(11);
        await assertion;
        expect(browser.revoke).toHaveBeenCalledOnce();
        expect(browser.canvases[0].width).toBe(0);
    });

    it("отмена ожидающего экспорта очищает ресурсы, предварительная отмена не создаёт URL", async () => {
        const browser = pngBrowser("hang-load");
        const controller = new AbortController();
        const pending = exportPNG(scene(shape("a")), { signal: controller.signal });
        controller.abort();
        await expect(pending).rejects.toThrow("отменён");
        expect(browser.revoke).toHaveBeenCalledOnce();
        await expect(exportPNG(scene(shape("a")), { signal: controller.signal })).rejects.toThrow("отменён");
        expect(browser.create).toHaveBeenCalledOnce();
    });

    it("ошибка URL creation очищает уже выделенный canvas", async () => {
        const browser = pngBrowser();
        browser.create.mockImplementation(() => { throw new Error("url failed"); });
        await expect(exportPNG(scene(shape("a")))).rejects.toThrow("url failed");
        expect(browser.canvases[0].width).toBe(0);
    });
});
