import { afterEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { readHistoryWeek, shiftHistoryDay } from "./useProjectHistory";

afterEach(() => vi.restoreAllMocks());
const signal = () => new AbortController().signal;
it("loads a complete local week across API pages and finds the next nonempty window", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValueOnce({ items: [{ id: "a" }], next_offset: 100 })
        .mockResolvedValueOnce({ items: [{ id: "a" }, { id: "b" }], next_offset: null })
        .mockResolvedValueOnce({ items: [{ id: "old", created_at: "2026-10-30T22:00:00Z" }], next_offset: null });
    const week = await readHistoryWeek(1, { kind: "task_report" }, "Europe/Samara", "2026-11-20", null, signal());
    expect(week).toMatchObject({ since: "2026-11-14", until: "2026-11-20", nextUntil: "2026-10-31", items: [{ id: "a" }, { id: "b" }] });
    const queries = get.mock.calls.map(([path]) => new URL(path, "http://local").searchParams);
    expect(queries[0].get("since")).toBe("2026-11-14");
    expect(queries[1].get("offset")).toBe("100");
    expect(queries[2].get("until")).toBe("2026-11-13");
    expect(queries.every(query => query.get("kind") === "task_report")).toBe(true);
});
it("honors the selected period and never probes outside its lower bound", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({ items: [], next_offset: null });
    const week = await readHistoryWeek(1, { since: "2026-11-17", until: "2026-11-20" }, "UTC", "2026-11-20", null, signal());
    expect(week.since).toBe("2026-11-17"); expect(week.nextUntil).toBeNull(); expect(get).toHaveBeenCalledTimes(1);
    expect(shiftHistoryDay("2026-03-09", -6)).toBe("2026-03-03");
});
it("loads an exact event independently of period filters", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({ items: [{ id: "task_report:5" }], next_offset: null });
    await readHistoryWeek(1, { q: "other", since: "2026-11-01" }, "UTC", "2026-11-20", "task_report:5", signal());
    const query = new URL(get.mock.calls[0][0], "http://local").searchParams;
    expect(query.get("event_id")).toBe("task_report:5"); expect(query.has("since")).toBe(false); expect(query.has("q")).toBe(false);
    expect(get).toHaveBeenCalledTimes(1);
});
it("stops a cancelled load before requesting another page and never returns a partial week", async () => {
    const controller = new AbortController();
    const get = vi.spyOn(api, "get").mockImplementation(async () => { controller.abort(); return { items: [], next_offset: 100 }; });
    await expect(readHistoryWeek(1, {}, "UTC", "2026-11-20", null, controller.signal)).rejects.toThrow();
    expect(get).toHaveBeenCalledTimes(1);
    get.mockReset().mockResolvedValueOnce({ items: [{ id: "a" }], next_offset: 100 }).mockRejectedValueOnce(new Error("offline"));
    await expect(readHistoryWeek(1, {}, "UTC", "2026-11-20", null, signal())).rejects.toThrow("offline");
});
