import { afterEach, expect, it, vi } from "vitest";
import { api } from "./api";
import { readHistoryWeek, shiftHistoryDay } from "./useProjectHistory";

afterEach(() => vi.restoreAllMocks());
const signal = () => new AbortController().signal;
it("loads a complete local week across API pages and finds the next nonempty window", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValueOnce({ items: [{ id: "a" }], next_offset: 100, previous_day: "2026-10-31" })
        .mockResolvedValueOnce({ items: [{ id: "a" }, { id: "b" }], next_offset: null, previous_day: null });
    const week = await readHistoryWeek(1, { kind: "task_report" }, "Europe/Samara", "2026-11-20", null, signal());
    expect(week).toMatchObject({ since: "2026-11-14", until: "2026-11-20", nextUntil: "2026-10-31", items: [{ id: "a" }, { id: "b" }] });
    const queries = get.mock.calls.map(([path]) => new URL(path, "http://local").searchParams);
    expect(queries[0].get("since")).toBe("2026-11-14");
    expect(queries[1].get("offset")).toBe("100");
    expect(queries).toHaveLength(2);
    expect(queries[0].get("include_previous")).toBe("true");
    expect(queries[1].has("include_previous")).toBe(false);
    expect(queries.every(query => query.get("kind") === "task_report")).toBe(true);
});
it("honors the selected period and never probes outside its lower bound", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({ items: [], next_offset: null });
    const week = await readHistoryWeek(1, { since: "2026-11-17", until: "2026-11-20" }, "UTC", "2026-11-20", null, signal());
    expect(week.since).toBe("2026-11-17"); expect(week.nextUntil).toBeNull(); expect(get).toHaveBeenCalledTimes(1);
    expect(new URL(get.mock.calls[0][0], "http://local").searchParams.has("include_previous")).toBe(false);
    expect(shiftHistoryDay("2026-03-09", -6)).toBe("2026-03-03");
});
it("loads an exact event independently of period filters", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({ items: [{ id: "task_report:5" }], next_offset: null });
    await readHistoryWeek(1, { q: "other", since: "2026-11-01" }, "UTC", "2026-11-20", "task_report:5", signal());
    const query = new URL(get.mock.calls[0][0], "http://local").searchParams;
    expect(query.get("event_id")).toBe("task_report:5"); expect(query.has("since")).toBe(false); expect(query.has("q")).toBe(false);
    expect(query.has("include_previous")).toBe(false);
    expect(get).toHaveBeenCalledTimes(1);
});

it.each(["2026-10-30", "2026-11-14"])("ignores previous day %s outside the remaining selected period", async previous_day => {
    const get = vi.spyOn(api, "get").mockResolvedValue({ items: [], next_offset: null, previous_day });
    const week = await readHistoryWeek(1, { since: "2026-11-01" }, "UTC", "2026-11-20", null, signal());
    expect(week.nextUntil).toBeNull();
    expect(get).toHaveBeenCalledTimes(1);
});

it("publishes the complete week without an extra request for older history", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({ items: [{ id: "a" }], next_offset: null, previous_day: "2026-10-31" });
    const week = await readHistoryWeek(1, {}, "Europe/Samara", "2026-11-20", null, signal());
    expect(week.items).toEqual([{ id: "a" }]);
    expect(week.nextUntil).toBe("2026-10-31");
    expect(get).toHaveBeenCalledTimes(1);
});
it("stops a cancelled load before requesting another page and never returns a partial week", async () => {
    const controller = new AbortController();
    const get = vi.spyOn(api, "get").mockImplementation(async () => { controller.abort(); return { items: [], next_offset: 100 }; });
    await expect(readHistoryWeek(1, {}, "UTC", "2026-11-20", null, controller.signal)).rejects.toThrow();
    expect(get).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledWith(expect.any(String), { signal: controller.signal });
    get.mockReset().mockResolvedValueOnce({ items: [{ id: "a" }], next_offset: 100 }).mockRejectedValueOnce(new Error("offline"));
    await expect(readHistoryWeek(1, {}, "UTC", "2026-11-20", null, signal())).rejects.toThrow("offline");
});

it("passes cancellation through the API client to the pending fetch", async () => {
    const controller = new AbortController();
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation((_url, init) => new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(init.signal!.reason), { once: true });
    }));
    const loading = readHistoryWeek(1, {}, "UTC", "2026-11-20", null, controller.signal);
    const cancelled = expect(loading).rejects.toMatchObject({ name: "AbortError" });
    controller.abort();
    await cancelled;
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][1]?.signal).toBe(controller.signal);
});
