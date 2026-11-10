/// <reference types="node" />
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { MIN_TEXT, clampOklch, contrastRatio, mixHex, parseThemeNumber, parseThemeTokens } from "@/lib/board/colorContrast.test-support";
import {
    DEFAULT_STICKER_COLOR,
    STICKER_COLOR_PRESETS,
    normalizeStickerColor,
    stickerColorLabel,
    stickerSurface,
} from "@/lib/board/stickers";

const here = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(resolve(here, "../../app.css"), "utf8");
const trackerCss = readFileSync(resolve(here, "../../components/board/board.css"), "utf8");

/*
 * Цвет стикера выбирает человек — любой (Кирилл Барабанщиков, 29.09.2026). Фон рисует
 * stickerSurface(): светлота OKLCH зажата в [--note-l-min; --note-l-max], насыщенность ≤
 * --note-c-max. Перебираем цвета по всему кругу оттенков и светлоте и меряем, что текст
 * стикера (--surface-note-ink) и его приглушённые подписи читаются ≥ 4.5 в теме личного проекта.
 */
const themes = [
    ["тема личного проекта", ":root {"],
] as const;

function hsl(h: number, s: number, l: number): string {
    const k = (n: number) => (n + h / 30) % 12;
    const a = s * Math.min(l, 1 - l);
    const f = (n: number) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
    return `#${[f(0), f(8), f(4)].map((v) => Math.round(v * 255).toString(16).padStart(2, "0")).join("")}`;
}

const SAMPLES = [
    "#000000", "#ffffff", "#7c1623", "#ffff00", "#00ff00", "#0000ff", "#ff00ff", "#00ffff", "#ff0000",
    ...STICKER_COLOR_PRESETS.map((option) => option.value),
    ...Array.from({ length: 24 }, (_, i) => i * 15).flatMap((h) =>
        [0.1, 0.3, 0.5, 0.7, 0.9].flatMap((l) => [0.3, 1].map((s) => hsl(h, s, l))),
    ),
];

// Самая бледная подпись стикера — дата и автор: --surface-note-ink с прозрачностью 25% (app.css).
const FAINTEST_INK_SHARE = 0.75;

describe.each(themes)("%s: текст на стикере любого цвета", (_name, selector) => {
    const tokens = parseThemeTokens(css, selector);
    const lMin = parseThemeNumber(trackerCss, selector, "note-l-min");
    const lMax = parseThemeNumber(trackerCss, selector, "note-l-max");
    const cMax = parseThemeNumber(trackerCss, selector, "note-c-max");
    const ink = tokens["surface-note-ink"];

    it.each(SAMPLES)("%s", (color) => {
        const surface = clampOklch(color, lMin, lMax, cMax);
        expect(contrastRatio(ink, surface)).toBeGreaterThanOrEqual(MIN_TEXT);
        expect(contrastRatio(mixHex(ink, surface, FAINTEST_INK_SHARE), surface)).toBeGreaterThanOrEqual(MIN_TEXT);
    });
});

describe("светлая тема: бумажные цвета пресетов", () => {
    const lMin = parseThemeNumber(trackerCss, ":root {", "note-l-min");
    const lMax = parseThemeNumber(trackerCss, ":root {", "note-l-max");
    const cMax = parseThemeNumber(trackerCss, ":root {", "note-c-max");
    it.each(STICKER_COLOR_PRESETS)("$label", ({ value }) => {
        const surface = clampOklch(value, lMin, lMax, cMax);
        // ±2 на канал — погрешность перевода туда-обратно.
        const channels = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
        channels(surface).forEach((v, i) => expect(Math.abs(v - channels(value)[i])).toBeLessThanOrEqual(2));
    });
});

describe("цвет стикера", () => {
    it("hex приводится к нижнему регистру, неверный цвет заменяется умолчанием", () => {
        expect(normalizeStickerColor("#7C1623")).toBe("#7c1623");
        expect(normalizeStickerColor("blue")).toBe(DEFAULT_STICKER_COLOR);
        expect(normalizeStickerColor("не цвет")).toBe(DEFAULT_STICKER_COLOR);
        expect(normalizeStickerColor(undefined)).toBe(DEFAULT_STICKER_COLOR);
    });

    it("фон — выбранный цвет с пределами темы, а не сырой hex", () => {
        expect(stickerSurface("#7C1623")).toBe(
            "oklch(from #7c1623 clamp(var(--note-l-min), l, var(--note-l-max)) min(c, var(--note-c-max)) h)",
        );
    });

    it("подпись: имя пресета или hex", () => {
        expect(stickerColorLabel("#C9EDFF")).toBe("Синий");
        expect(stickerColorLabel("#123456")).toBe("#123456");
    });
});
