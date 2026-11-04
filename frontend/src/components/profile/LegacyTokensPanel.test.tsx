// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { LegacyTokensPanel } from "./LegacyTokensPanel";
import { api, authEndpoints, queryKeys } from "@/lib/api";
import type { ApiToken } from "@/lib/types";

const token: ApiToken = { id: 1, name: "Существующий клиент", prefix: "tt_demo", scope: "READ", created_at: "2026-11-04T06:00:00Z", expires_at: null, revoked_at: null, last_used_at: null };
const clients: QueryClient[] = [];
function setup() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    clients.push(client);
    client.setQueryData(queryKeys.apiTokens, [token]);
    vi.spyOn(api, "get").mockResolvedValue([token]);
    render(<QueryClientProvider client={client}><LegacyTokensPanel /></QueryClientProvider>);
}
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); });

it("по умолчанию выпускает READ-токен тем же API и показывает секрет один раз", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue({ token, secret: "SYNTHETIC_TOKEN_ONCE" });
    setup();
    await userEvent.type(screen.getByLabelText("Название токена"), "Ноутбук");
    await userEvent.click(screen.getByRole("button", { name: "Выпустить токен" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith(authEndpoints.apiTokens(), { name: "Ноутбук", scope: "READ", ttl_days: 90 }));
    expect(await screen.findByText("SYNTHETIC_TOKEN_ONCE")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Я сохранил токен" }));
    expect(screen.queryByText("SYNTHETIC_TOKEN_ONCE")).toBeNull();
});
it("отзывает токен только после отдельного подтверждения", async () => {
    const remove = vi.spyOn(api, "delete").mockResolvedValue(undefined);
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Отозвать токен Существующий клиент" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Отмена" }));
    expect(remove).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Отозвать токен Существующий клиент" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Отозвать" }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith(authEndpoints.apiToken(1)));
});
