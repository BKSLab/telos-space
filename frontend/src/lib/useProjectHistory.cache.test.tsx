// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { focusManager, onlineManager, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, ApiError, queryKeys } from "./api";
import { useProjectHistory } from "./useProjectHistory";

const clients: QueryClient[] = [];
let historyRevision = "0";
let versionError: Error | null = null;
let versionReads = 0;
beforeEach(() => { historyRevision = "0"; versionError = null; versionReads = 0; });
function historySpy() {
    const get = vi.fn<(path: string, options?: Pick<RequestInit, "signal">) => Promise<unknown>>();
    vi.spyOn(api, "get").mockImplementation(async (path, options) => {
        if (path.endsWith("/history/version")) {
            versionReads += 1;
            if (versionError) throw versionError;
            return { revision: historyRevision };
        }
        const page = await get(path, options) as object;
        return { revision: historyRevision, ...page };
    });
    return get;
}
function setup() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    clients.push(client);
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    return { client, renderHistory: (days: string[] = []) => renderHook(({ visible }) => ({ ...useProjectHistory(1, {}, "UTC", "2026-11-20", null, visible) }), { initialProps: { visible: days }, wrapper }), wrapper };
}
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); focusManager.setFocused(undefined); onlineManager.setOnline(true); vi.restoreAllMocks(); vi.useRealTimers(); });

it("starts the history window without waiting for the version response", async () => {
    let finishVersion!: () => void;
    const version = new Promise(resolve => { finishVersion = () => resolve({ revision: "0" }); });
    const reads: string[] = [];
    vi.spyOn(api, "get").mockImplementation(async path => {
        reads.push(path);
        if (path.endsWith("/history/version")) return version;
        return { items: [{ id: "already-visible" }], next_offset: null, previous_day: null, revision: "0" };
    });
    const { renderHistory } = setup();
    const view = renderHistory();
    await waitFor(() => expect(view.result.current.data?.pages[0].items).toEqual([{ id: "already-visible" }]));
    expect(reads).toHaveLength(2);
    expect(view.result.current.isFetching).toBe(true);
    await act(async () => { finishVersion(); });
    await waitFor(() => expect(view.result.current.isFetching).toBe(false));
    expect(reads).toHaveLength(2);
});

it("cancels and rejects a late window response after the parallel version read denies access", async () => {
    let rejectVersion!: (reason: Error) => void;
    let finishWindow!: () => void;
    let windowSignal: AbortSignal | undefined;
    const version = new Promise((_resolve, reject) => { rejectVersion = reject; });
    const window = new Promise(resolve => { finishWindow = () => resolve({ items: [{ id: "private" }], next_offset: null, previous_day: null, revision: "0" }); });
    vi.spyOn(api, "get").mockImplementation(async (path, options) => {
        if (path.endsWith("/history/version")) return version;
        windowSignal = options!.signal!;
        return window;
    });
    const { renderHistory } = setup();
    const view = renderHistory();
    await waitFor(() => expect(windowSignal).toBeDefined());
    await act(async () => { rejectVersion(new ApiError(403, "denied")); });
    await waitFor(() => expect(view.result.current.error?.message).toBe("denied"));
    expect(windowSignal!.aborted).toBe(true);
    await act(async () => { finishWindow(); });
    expect(view.result.current.data?.pages.flatMap(page => page.items) ?? []).toEqual([]);
    expect(view.result.current.error?.message).toBe("denied");
});

it("checks only the version on focus and reconnect until the journal changes", async () => {
    const get = historySpy().mockResolvedValue({ items: [{ id: "a" }], next_offset: null, previous_day: null });
    const { renderHistory } = setup();
    const view = renderHistory();
    await waitFor(() => expect(view.result.current.isSuccess).toBe(true));
    await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true); });
    await waitFor(() => expect(versionReads).toBe(2));
    expect(get).toHaveBeenCalledTimes(1);
    await act(async () => { onlineManager.setOnline(false); onlineManager.setOnline(true); });
    await waitFor(() => expect(versionReads).toBe(3));
    expect(get).toHaveBeenCalledTimes(1);
    historyRevision = "2";
    await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true); });
    await waitFor(() => expect(view.result.current.data?.pages[0].revision).toBe("2"));
    expect(get).toHaveBeenCalledTimes(2);
});

it("polls a small version every 30 seconds and reads no unchanged archive payloads", async () => {
    vi.useFakeTimers();
    const get = historySpy().mockResolvedValue({ items: [{ id: "a" }], next_offset: null, previous_day: null });
    const { renderHistory } = setup();
    const view = renderHistory();
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(view.result.current.isSuccess).toBe(true);
    expect(get).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    expect(versionReads).toBe(2);
    expect(get).toHaveBeenCalledTimes(1);
    historyRevision = "2";
    await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(view.result.current.data?.pages[0].revision).toBe("2");
    expect(get).toHaveBeenCalledTimes(2);
});

it("accepts a window revision newer than the last poll without repeatedly fetching it", async () => {
    const get = historySpy().mockResolvedValue({ items: [{ id: "a" }], next_offset: null, previous_day: null, revision: "9007199254740993" });
    const { client, renderHistory } = setup();
    const view = renderHistory();
    await waitFor(() => expect(view.result.current.isSuccess).toBe(true));
    historyRevision = "9007199254740992";
    await act(async () => { await client.refetchQueries({ queryKey: [...queryKeys.project(1), "history-version"] }); });
    expect(get).toHaveBeenCalledTimes(1);
    expect(view.result.current.data?.pages[0].revision).toBe("9007199254740993");
});

it("clears every project history filter after a version denial and restores only after successful access", async () => {
    const get = historySpy().mockResolvedValue({ items: [{ id: "private" }], next_offset: null, previous_day: null });
    const { client, renderHistory } = setup();
    const first = renderHistory();
    await waitFor(() => expect(first.result.current.isSuccess).toBe(true));
    const otherKey = [...queryKeys.project(1), "history-weeks", { kind: "task_comment" }, "UTC", "2026-11-20", null];
    client.setQueryData(otherKey, { pages: [{ items: [{ id: "other-private" }] }], pageParams: ["2026-11-20"] });
    versionError = new ApiError(403, "denied");
    await act(async () => { await client.refetchQueries({ queryKey: [...queryKeys.project(1), "history-version"] }); });
    await waitFor(() => expect(first.result.current.error?.message).toBe("denied"));
    expect(first.result.current.data?.pages).toEqual([]);
    expect(client.getQueryData(otherKey)).toEqual({ pages: [], pageParams: [] });
    versionError = new Error("offline");
    await act(async () => { await client.refetchQueries({ queryKey: [...queryKeys.project(1), "history-version"] }); });
    await waitFor(() => expect(first.result.current.error?.message).toBe("offline"));
    expect(first.result.current.data?.pages).toEqual([]);
    expect(get).toHaveBeenCalledTimes(1);
    versionError = null;
    get.mockResolvedValue({ items: [{ id: "allowed-again" }], next_offset: null, previous_day: null });
    await act(async () => { await first.result.current.refetch(); });
    await waitFor(() => expect(first.result.current.data?.pages[0].items).toEqual([{ id: "allowed-again" }]));
});

it("reuses archive windows and refreshes changed versions only when visible", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-20T12:00:00Z"));
    let revision = 1;
    const get = historySpy().mockImplementation(async path => {
        const until = new URL(path, "http://local").searchParams.get("until");
        return { items: [{ id: until, title: `Версия ${revision}` }], next_offset: null, previous_day: until === "2026-11-20" ? "2026-11-10" : null };
    });
    const { renderHistory } = setup();
    const first = renderHistory();
    await waitFor(() => expect(first.result.current.isSuccess).toBe(true));
    await act(async () => { await first.result.current.fetchNextPage(); });
    await waitFor(() => expect(first.result.current.data?.pages).toHaveLength(2));
    expect(get).toHaveBeenCalledTimes(2);
    first.unmount();
    const quickReturn = renderHistory();
    expect(quickReturn.result.current.data?.pages).toHaveLength(2);
    await waitFor(() => expect(quickReturn.result.current.isFetching).toBe(false));
    expect(get).toHaveBeenCalledTimes(2);
    quickReturn.unmount();
    revision = 2;
    historyRevision = "2";
    vi.setSystemTime(new Date("2026-11-20T12:01:01Z"));
    const staleReturn = renderHistory();
    // Cached dates remain available while the refreshed windows are loading.
    expect(staleReturn.result.current.data?.pages[1].items[0].title).toBe("Версия 1");
    await waitFor(() => expect(staleReturn.result.current.data?.pages[0].items[0].title).toBe("Версия 2"));
    expect(staleReturn.result.current.data?.pages[1].items[0].title).toBe("Версия 1");
    expect(get).toHaveBeenCalledTimes(3);
    staleReturn.rerender({ visible: ["2026-11-10"] });
    await waitFor(() => expect(staleReturn.result.current.data?.pages[1].items[0].title).toBe("Версия 2"));
    expect(get).toHaveBeenCalledTimes(4);
});

it("refreshes a fresh cached window when its project is invalidated", async () => {
    const get = historySpy().mockResolvedValue({ items: [{ id: "a" }], next_offset: null, previous_day: null });
    const { client, renderHistory } = setup();
    const view = renderHistory();
    await waitFor(() => expect(view.result.current.isSuccess).toBe(true));
    get.mockResolvedValue({ items: [], next_offset: null, previous_day: null });
    historyRevision = "2";
    await act(async () => { await client.invalidateQueries({ queryKey: queryKeys.project(1) }); });
    await waitFor(() => expect(view.result.current.data?.pages[0].items).toEqual([]));
    expect(get).toHaveBeenCalledTimes(2);
});

it.each(["refresh", "older window"] as const)("clears denied contents after %s and keeps them cleared across offline retry and remount", async operation => {
    const get = historySpy().mockResolvedValue({ items: [{ id: "private" }], next_offset: null, previous_day: "2026-11-10" });
    const { renderHistory } = setup();
    const first = renderHistory();
    await waitFor(() => expect(first.result.current.isSuccess).toBe(true));
    get.mockRejectedValue(new ApiError(403, "Доступ отозван"));
    await act(async () => {
        if (operation === "refresh") await first.result.current.refetch();
        else await first.result.current.fetchNextPage();
    });
    await waitFor(() => expect(first.result.current.error).toBeInstanceOf(ApiError));
    expect(first.result.current.data?.pages).toEqual([]);
    get.mockRejectedValue(new Error("offline"));
    await act(async () => { await first.result.current.refetch(); });
    await waitFor(() => expect(first.result.current.error?.message).toBe("offline"));
    expect(first.result.current.data?.pages).toEqual([]);
    first.unmount();
    const returned = renderHistory();
    expect(returned.result.current.data?.pages).toEqual([]);
    await waitFor(() => expect(returned.result.current.isFetching).toBe(false));
    expect(returned.result.current.data?.pages).toEqual([]);
    get.mockResolvedValue({ items: [{ id: "allowed-again" }], next_offset: null, previous_day: null });
    await act(async () => { await returned.result.current.refetch(); });
    await waitFor(() => expect(returned.result.current.data?.pages[0].items).toEqual([{ id: "allowed-again" }]));
    expect(returned.result.current.error).toBeNull();
});

it("waits for every API page before publishing a day's events and counts", async () => {
    let finish!: () => void;
    const items = Array.from({ length: 100 }, (_, id) => ({ id: String(id), created_at: "2026-11-20T10:00:00Z" }));
    const get = historySpy().mockResolvedValueOnce({ items, next_offset: 100, previous_day: null })
        .mockImplementationOnce(() => new Promise(resolve => { finish = () => resolve({ items: [{ id: "100", created_at: "2026-11-20T09:00:00Z" }], next_offset: null, previous_day: null }); }));
    const { renderHistory } = setup();
    const view = renderHistory();
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    expect(view.result.current.isPending).toBe(true);
    expect(view.result.current.data).toBeUndefined();
    await act(async () => { finish(); });
    await waitFor(() => expect(view.result.current.data?.pages[0].items).toHaveLength(101));
});

it("cancels an obsolete filter request and never caches its partial results", async () => {
    const requests: AbortSignal[] = [];
    historySpy().mockImplementation(async (_path, options) => {
        const requestSignal = options!.signal!;
        requests.push(requestSignal);
        if (requests.length === 1) return new Promise((_resolve, reject) => requestSignal.addEventListener("abort", () => reject(requestSignal.reason), { once: true }));
        return { items: [{ id: "matching" }], next_offset: null, previous_day: null };
    });
    const { client, wrapper } = setup();
    const view = renderHook(({ q }) => useProjectHistory(1, { q }, "UTC", "2026-11-20", null), { initialProps: { q: "old" }, wrapper });
    await waitFor(() => expect(requests).toHaveLength(1));
    view.rerender({ q: "new" });
    await waitFor(() => expect(view.result.current.isSuccess).toBe(true));
    expect(requests[0].aborted).toBe(true);
    expect(view.result.current.data?.pages[0].items).toEqual([{ id: "matching" }]);
    const obsolete = client.getQueryCache().findAll({ queryKey: queryKeys.project(1) }).find(query => JSON.stringify(query.queryKey).includes('"old"'));
    expect(obsolete?.state.data).toBeUndefined();
});
