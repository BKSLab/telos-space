// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import type { ProjectMember } from "@/lib/types";
import { ProjectRisksPage } from "./ProjectRisksPage";

vi.mock("@/lib/useProjectOutlet", () => ({ useProjectOutlet: () => ({ id: 1 }) }));
vi.mock("@/lib/useRisks", () => ({ useRiskSummary: () => ({ isPending: false, error: null, data: undefined }) }));
vi.mock("@/components/risks/RiskSuggestions", () => ({ RiskSuggestions: () => null }));
const clients: QueryClient[] = [];
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("фильтр с фотографией передаёт user.id и снимается через Все участники", async () => {
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    const queries: URLSearchParams[] = [];
    const members: ProjectMember[] = [{ id: 901, project_id: 1, role: "MEMBER", created_at: "2026-11-15T10:00:00Z", user: { id: 7, username: "oleg", first_name: "Олег", last_name: "Петров", middle_name: null, has_avatar: true } }];
    vi.spyOn(globalThis, "fetch").mockImplementation(async input => {
        const url = new URL(String(input), "http://localhost");
        let data: unknown = [];
        if (url.pathname.endsWith("/members")) data = members;
        if (url.pathname.endsWith("/risks")) { queries.push(url.searchParams); data = { items: [], total: 0, page: 1, page_size: 20 }; }
        return new Response(JSON.stringify(data), { status: 200, headers: { "content-type": "application/json" } });
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><MemoryRouter><ProjectRisksPage /></MemoryRouter></QueryClientProvider>);
    const owner = screen.getByRole("button", { name: "Ответственный" });
    await waitFor(() => expect((owner as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(owner);
    const option = within(screen.getByRole("dialog", { name: "Ответственный" })).getByRole("option", { name: "Петров Олег" });
    expect(option.querySelector('img[src*="/users/7/avatar"]')).toBeTruthy();
    fireEvent.click(option);
    await waitFor(() => expect(queries.at(-1)?.get("owner_user_id")).toBe("7"));
    expect(queries.at(-1)?.get("page")).toBe("1");
    fireEvent.click(owner);
    fireEvent.click(within(screen.getByRole("dialog", { name: "Ответственный" })).getByRole("option", { name: "Все участники" }));
    await waitFor(() => expect(queries.at(-1)?.has("owner_user_id")).toBe(false));
});
