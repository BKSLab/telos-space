import { afterEach, expect, it, vi } from "vitest";
import { browserTimezone } from "./timezone";

afterEach(() => vi.unstubAllGlobals());

it("берёт IANA timezone браузера", () => {
    vi.stubGlobal("Intl", { DateTimeFormat: () => ({ resolvedOptions: () => ({ timeZone: "Europe/Samara" }) }) });
    expect(browserTimezone()).toBe("Europe/Samara");
});

it.each(["missing", "error"])("использует UTC при недоступной timezone: %s", mode => {
    vi.stubGlobal("Intl", { DateTimeFormat: () => { if (mode === "error") throw new Error("Intl unavailable"); return { resolvedOptions: () => ({}) }; } });
    expect(browserTimezone()).toBe("UTC");
});
