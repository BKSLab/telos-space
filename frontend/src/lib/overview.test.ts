import { describe, expect, it } from "vitest";
import { latestUpdate, reportFreshness } from "@/lib/overview";

describe("reportFreshness", () => {
    it("без разбора возвращает none", () => {
        expect(reportFreshness(null, "2026-09-06T10:00:00Z")).toBe("none");
        expect(reportFreshness(undefined, "2026-09-06T10:00:00Z")).toBe("none");
    });

    it("считает разбор устаревшим, когда данные изменились после него", () => {
        expect(reportFreshness("2026-09-06T10:00:00Z", "2026-09-06T10:30:00Z")).toBe("stale");
    });

    it("считает разбор свежим, когда данные не менялись после него", () => {
        expect(reportFreshness("2026-09-06T10:30:00Z", "2026-09-06T10:00:00Z")).toBe("fresh");
        expect(reportFreshness("2026-09-06T10:00:00Z", "2026-09-06T10:00:00Z")).toBe("fresh");
    });

    it("без времени данных не объявляет разбор устаревшим", () => {
        // Отсутствие отметки — не доказательство изменений: пугать
        // пользователя на пустом месте хуже, чем промолчать.
        expect(reportFreshness("2026-09-06T10:00:00Z", null)).toBe("fresh");
        expect(reportFreshness("2026-09-06T10:00:00Z", "не дата")).toBe("fresh");
    });
});

describe("latestUpdate", () => {
    it("возвращает самую позднюю отметку набора", () => {
        expect(
            latestUpdate([
                { updated_at: "2026-09-01T10:00:00Z" },
                { updated_at: "2026-09-06T09:00:00Z" },
                { updated_at: "2026-09-03T23:00:00Z" },
            ]),
        ).toBe("2026-09-06T09:00:00Z");
    });

    it("пропускает пустые и некорректные значения", () => {
        expect(latestUpdate([{ updated_at: null }, { updated_at: "не дата" }])).toBeNull();
        expect(latestUpdate([])).toBeNull();
    });
});
