import { describe, expect, it } from "vitest";
import {
    buildMcpConfig,
    isTokenActive,
    mcpServerUrl,
    scopeLabel,
    SECRET_PLACEHOLDER,
    tokenState,
} from "@/lib/mcpConfig";
import type { ApiToken } from "@/lib/types";

function token(overrides: Partial<ApiToken> = {}): ApiToken {
    return {
        id: 1,
        name: "Ноутбук",
        prefix: "Abc12345",
        scope: "READ",
        created_at: "2026-09-01T10:00:00Z",
        expires_at: null,
        revoked_at: null,
        last_used_at: null,
        ...overrides,
    };
}

describe("mcpServerUrl", () => {
    it("собирает адрес из origin страницы", () => {
        expect(mcpServerUrl("http://localhost:5173")).toBe("http://localhost:5173/mcp");
    });

    it("не удваивает слэш", () => {
        expect(mcpServerUrl("http://localhost:5173/")).toBe("http://localhost:5173/mcp");
    });

    it("учитывает нестандартный путь", () => {
        expect(mcpServerUrl("https://tracker.example", "/tools")).toBe("https://tracker.example/tools");
    });
});

describe("buildMcpConfig", () => {
    it("подставляет секрет сразу после выпуска", () => {
        const config = JSON.parse(
            buildMcpConfig({ origin: "http://localhost:5173", secret: "telos_token_secret" }),
        );

        expect(config.mcpServers["telos-space"].headers.Authorization).toBe("Bearer telos_token_secret");
        expect(config.mcpServers["telos-space"].url).toBe("http://localhost:5173/mcp");
    });

    it("ставит плейсхолдер, когда секрета нет", () => {
        const config = JSON.parse(buildMcpConfig({ origin: "http://localhost:5173" }));

        expect(config.mcpServers["telos-space"].headers.Authorization).toBe(
            `Bearer ${SECRET_PLACEHOLDER}`,
        );
    });

    it("не подставляет секрет из пробелов", () => {
        const config = JSON.parse(
            buildMcpConfig({ origin: "http://localhost:5173", secret: "   " }),
        );

        expect(config.mcpServers["telos-space"].headers.Authorization).toContain(
            SECRET_PLACEHOLDER,
        );
    });

    it("отдаёт валидный JSON с отступами для копирования", () => {
        const raw = buildMcpConfig({ origin: "http://localhost:5173" });

        expect(raw).toContain("\n");
        expect(() => JSON.parse(raw)).not.toThrow();
    });
});

describe("scopeLabel", () => {
    it("описывает права по-русски", () => {
        expect(scopeLabel("READ")).toBe("Только чтение");
        expect(scopeLabel("WRITE")).toBe("Чтение и запись");
    });
});

describe("isTokenActive и tokenState", () => {
    const now = new Date("2026-09-02T12:00:00Z");

    it("бессрочный неотозванный токен действует", () => {
        expect(isTokenActive(token(), now)).toBe(true);
        expect(tokenState(token(), now)).toBe("Действует");
    });

    it("отозванный токен не действует", () => {
        const revoked = token({ revoked_at: "2026-09-02T11:00:00Z" });

        expect(isTokenActive(revoked, now)).toBe(false);
        expect(tokenState(revoked, now)).toBe("Отозван");
    });

    it("истёкший токен не действует", () => {
        const expired = token({ expires_at: "2026-09-01T00:00:00Z" });

        expect(isTokenActive(expired, now)).toBe(false);
        expect(tokenState(expired, now)).toBe("Истёк");
    });

    it("токен с будущим сроком действует", () => {
        const future = token({ expires_at: "2026-12-01T00:00:00Z" });

        expect(isTokenActive(future, now)).toBe(true);
        expect(tokenState(future, now)).toBe("Действует");
    });

    it("отзыв важнее срока", () => {
        const both = token({ expires_at: "2026-12-01T00:00:00Z", revoked_at: "2026-09-02T11:00:00Z" });

        expect(tokenState(both, now)).toBe("Отозван");
    });
});
