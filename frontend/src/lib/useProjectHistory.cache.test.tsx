// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { api, ApiError, queryKeys } from "./api";
import { useProjectHistory } from "./useProjectHistory";

const clients: QueryClient[] = [];
function setup() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    clients.push(client);
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    return { client, renderHistory: () => renderHook(() => ({ ...useProjectHistory(1, {}, "UTC", "2026-11-20", null) }), { wrapper }), wrapper };
}
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); vi.useRealTimers(); });

it("reuses loaded windows on quick return and refreshes old events once stale", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-20T12:00:00Z"));
    let revision = 1;
    const get = vi.spyOn(api, "get").mockImplementation(async path => {
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
    expect(quickReturn.result.current.isFetching).toBe(false);
    expect(get).toHaveBeenCalledTimes(2);
    quickReturn.unmount();
    revision = 2;
    vi.setSystemTime(new Date("2026-11-20T12:01:01Z"));
    const staleReturn = renderHistory();
    // Cached dates remain available while the refreshed windows are loading.
    expect(staleReturn.result.current.data?.pages[1].items[0].title).toBe("Версия 1");
    await waitFor(() => expect(staleReturn.result.current.data?.pages[1].items[0].title).toBe("Версия 2"));
    expect(get).toHaveBeenCalledTimes(4);
});

it("refreshes a fresh cached window when its project is invalidated", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({ items: [{ id: "a" }], next_offset: null, previous_day: null });
    const { client, renderHistory } = setup();
    const view = renderHistory();
    await waitFor(() => expect(view.result.current.isSuccess).toBe(true));
    get.mockResolvedValue({ items: [], next_offset: null, previous_day: null });
    await act(async () => { await client.invalidateQueries({ queryKey: queryKeys.project(1) }); });
    await waitFor(() => expect(view.result.current.data?.pages[0].items).toEqual([]));
    expect(get).toHaveBeenCalledTimes(2);
});

it.each(["refresh", "older window"] as const)("clears denied contents after %s and keeps them cleared across offline retry and remount", async operation => {
    const get = vi.spyOn(api, "get").mockResolvedValue({ items: [{ id: "private" }], next_offset: null, previous_day: "2026-11-10" });
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
    const get = vi.spyOn(api, "get").mockResolvedValueOnce({ items, next_offset: 100, previous_day: null })
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
    vi.spyOn(api, "get").mockImplementation(async (_path, options) => {
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
