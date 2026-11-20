// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, queryKeys } from "@/lib/api";
import type { KnowledgeClaim, KnowledgeMap } from "@/lib/projectKnowledge";
import { useUiStore } from "@/stores/ui";
import { ProjectKnowledgePanel } from "./ProjectKnowledgePanel";

const claim: KnowledgeClaim = {
    id: "a9d98927-7467-4017-a83e-fc01bf0b58ee", revision: 1, kind: "source_fact",
    category: "decisions", text: "Решение об импорте", state: "current", acceptance: "unreviewed",
    verification: "source_statement", source_ids: [], evidence: [], conflicts_with: [],
    updated_at: "2026-11-20T12:00:00Z", reason: null,
};
const map: KnowledgeMap = {
    project_id: 7, generation: 2, built_generation: 2, status: "ready", updated_at: "2026-11-20T12:00:00Z",
    items: [claim], gaps: [], total: 1, offset: 0, limit: 20, has_more: false, next_offset: null,
    coverage: { sources_total: 1, sources_current: 1, sources_pending: 0, sources_failed: 0,
        sources_skipped: 0, complete: true, claims_current: 1, claims_conflicting: 0, claims_stale: 0,
        chars_processed: 100, chars_total: 100, reasons: [], method: "grounded_literal_v1" },
};
const clients: QueryClient[] = [];
beforeEach(() => { vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} }); });
function Location() { return <output data-testid="location">{useLocation().search}</output>; }
function mount(url = "/knowledge") {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } });
    clients.push(client);
    client.setQueryData(queryKeys.currentUser, { id: 1 });
    return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[url]}>
        <ProjectKnowledgePanel projectId={7} projectKey="DEMO" ownerId={1} /><Location />
    </MemoryRouter></QueryClientProvider>);
}
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); vi.unstubAllGlobals(); useUiStore.setState({ selectedTaskId: null }); });

it("восстанавливает фильтры карты из URL и не меняет фильтры истории", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue(map);
    mount("/knowledge?mq=импорт&category=decisions&map_offset=20&q=отчёт&kind=task_report");
    await screen.findByRole("button", { name: "Источники и версии: Решение об импорте" });
    expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe("импорт");
    expect(get).toHaveBeenCalledWith(expect.stringContaining("offset=20"));
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "цель" } });
    fireEvent.click(screen.getByRole("button", { name: "Найти" }));
    await waitFor(() => expect(screen.getByTestId("location").textContent).toContain("mq=%D1%86%D0%B5%D0%BB%D1%8C"));
    expect(screen.getByTestId("location").textContent).toContain("kind=task_report");
    expect(screen.getByTestId("location").textContent).not.toContain("map_offset");
});

it("открывает прямую ссылку на знание вне текущей страницы через проверенный endpoint", async () => {
    vi.spyOn(api, "get").mockImplementation(async path => path.includes("/claims/") ? claim : { ...map, items: [], total: 0 });
    mount(`/knowledge?claim=${claim.id}`);
    expect(await screen.findByRole("dialog", { name: "Источники знания" })).toBeTruthy();
    expect(await screen.findByText("Решение об импорте")).toBeTruthy();
    expect(api.get).toHaveBeenCalledWith(`/api/v1/projects/7/knowledge/map/claims/${claim.id}`);
    fireEvent.click(screen.getByRole("button", { name: "Закрыть источники" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByTestId("location").textContent).not.toContain("claim=");
});

it("повторяет команду обновления после ошибки, а не только чтение карты", async () => {
    vi.spyOn(api, "get").mockResolvedValue(map);
    const post = vi.spyOn(api, "post").mockRejectedValueOnce(new Error("Очередь недоступна")).mockResolvedValueOnce({});
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Обновить знания" }));
    fireEvent.click(await screen.findByRole("button", { name: "Повторить обновление" }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(post.mock.calls[1][0]).toBe("/api/v1/projects/7/knowledge/map/refresh");
});
