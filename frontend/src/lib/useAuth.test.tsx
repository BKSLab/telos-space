// @vitest-environment jsdom
import { cleanup, renderHook, act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { api, queryKeys } from "./api";
import { useLogin, useRegister } from "./useAuth";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it.each(["login", "register"])("%s cancels pending reads and removes every previous user's cache", async kind => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    const user = { id: 2, username: "new-user" };
    vi.spyOn(api, "post").mockResolvedValue(user);
    client.setQueryData(queryKeys.task(10), { title: "private previous user" });
    client.setQueryData(["documents", "private"], ["previous files"]);
    let signal: AbortSignal | undefined;
    const read = client.fetchQuery({ queryKey: ["pending"], queryFn: context => {
        signal = context.signal;
        return new Promise(() => {});
    } }).catch(() => undefined);
    const { result } = renderHook(() => {
        const login = useLogin();
        const register = useRegister();
        return kind === "login" ? login : register;
    }, { wrapper: ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider> });
    act(() => result.current.mutate({ username: "new-user", password: "test-password", invite_code: "test", password_confirm: "test-password", last_name: "Test", first_name: "User" }));
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(signal?.aborted).toBe(true);
    await read;
    expect(client.getQueryData(queryKeys.currentUser)).toEqual(user);
    expect(client.getQueryCache().getAll().map(query => query.queryKey)).toEqual([queryKeys.currentUser]);
    client.clear();
});
