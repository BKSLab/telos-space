// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { api, ApiError, queryKeys } from "@/lib/api";
import type { GitConnection, KnowledgeMap } from "@/lib/projectKnowledge";
import { ProjectGitSources } from "./ProjectGitSources";
import { ProjectKnowledgePanel } from "./ProjectKnowledgePanel";

const clients: QueryClient[] = [];
function wrap(children: React.ReactNode) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } });
    clients.push(client);
    client.setQueryData(queryKeys.currentUser, { id: 1 });
    return <QueryClientProvider client={client}><MemoryRouter>{children}</MemoryRouter></QueryClientProvider>;
}
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); });

const map: KnowledgeMap = {
    project_id: 7, generation: 2, built_generation: 2, status: "partial", updated_at: "2026-11-16T12:00:00Z",
    items: [{ id: "a9d98927-7467-4017-a83e-fc01bf0b58ee", revision: 1, kind: "source_fact", category: "task_results", text: "Участник сообщил о выполнении импорта", state: "current", acceptance: "unreviewed", verification: "participant_report", source_ids: ["task_report:12"], evidence: [{ source_id: "task_report:12", native_revision: "2", source_hash: "a".repeat(64), content_hash: "b".repeat(64), title: "Опубликованный отчёт", quote: "Импорт реализован", start: 0, end: 17, document_ref: null, task_id: 12, checked_at: "2026-11-16T12:00:00Z", coverage: "full" }], conflicts_with: [], updated_at: "2026-11-16T12:00:00Z", reason: null }],
    gaps: [{ code: "no_product_proof", category: "task_results", message: "Нет независимого подтверждения работы продукта" }],
    coverage: { sources_total: 3, sources_current: 2, sources_pending: 1, sources_failed: 0, sources_skipped: 0, complete: false, claims_current: 1, claims_conflicting: 0, claims_stale: 0, chars_processed: 100, chars_total: 200, reasons: ["pending"], method: "grounded_literal_v1" }, total: 1, offset: 0, limit: 20, has_more: false, next_offset: null,
};

function connection(id: number, name: string, ref: string): GitConnection {
    return { id, project_id: 7, repository_id: 100 + id, provider: "github", owner: "example", repo: name.toLowerCase(), name, purpose: "", ref, enabled: true, is_private: false, share_private_with_project: false, has_credential: false, has_webhook_secret: false, revision: 3, generation: 4, credential_revision: 0, state: id === 2 ? "partial" : "ready", observed_sha: "a".repeat(40), indexed_sha: "b".repeat(40), observed_at: null, indexed_at: null, checked_at: null, last_error_code: null, coverage: { total_files: 10, cataloged_files: 10, indexed_files: 5, metadata_files: 5, metadata_counts: { binary_file: 5 }, failed_files: 0, truncated: id === 2 } };
}

it("показывает происхождение отчёта и неполноту карты без объявления продового результата", async () => {
    vi.spyOn(api, "get").mockImplementation(async path => path.includes("/claims/") ? map.items[0] : map);
    render(wrap(<ProjectKnowledgePanel projectId={7} projectKey="DEMO" ownerId={1} />));
    await screen.findByText("Участник сообщил о выполнении импорта");
    expect(screen.getByText("Отчёт участника")).toBeTruthy();
    expect(screen.getByText(/Неполный охват/)).toBeTruthy();
    fireEvent.click(screen.getByText(/Источники и версии/));
    expect(await screen.findByText("Импорт реализован")).toBeTruthy();
    expect(screen.getByText(/task_report:12 · версия 2/)).toBeTruthy();
    expect(screen.queryByText("Функция доступна на проде")).toBeNull();
});

it("оставляет просмотр карты участнику, а ручное обновление владельцу", async () => {
    vi.spyOn(api, "get").mockImplementation(async path => path.includes("/claims/") ? map.items[0] : map);
    render(wrap(<ProjectKnowledgePanel projectId={7} projectKey="DEMO" ownerId={8} />));
    await screen.findByText("Участник сообщил о выполнении импорта");
    expect(screen.queryByRole("button", { name: "Обновить знания" })).toBeNull();
});

it("изменяет ref одного из трёх репозиториев с его optimistic revision", async () => {
    const connections = [connection(1, "Frontend", "main"), connection(2, "Auth", "release"), connection(3, "Gateway", "stable")];
    vi.spyOn(api, "get").mockImplementation(async () => ({ items: connections, total: 3, offset: 0, limit: 20, next_offset: null }));
    const patch = vi.spyOn(api, "patch").mockImplementation(async (_, body) => { Object.assign(connections[0], body); return connections[0]; });
    render(wrap(<ProjectGitSources projectId={7} canEdit />));
    await screen.findByRole("heading", { name: "Frontend" });
    expect(screen.getByRole("heading", { name: "Auth" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Gateway" })).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "Настроить" })[0]);
    fireEvent.change(screen.getByLabelText("Ветка или ref"), { target: { value: "develop" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить источник" }));
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(patch.mock.calls[0]).toEqual(["/api/v1/projects/7/git/connections/1", expect.objectContaining({ ref: "develop", expected_revision: 3 })]);
    expect(connections[1].ref).toBe("release");
    expect(connections[2].ref).toBe("stable");
});

it("409 сохраняет черновик ref до явного выбора актуальных настроек", async () => {
    vi.spyOn(api, "get").mockResolvedValue({ items: [connection(1, "Frontend", "main")], total: 1, offset: 0, limit: 20, next_offset: null });
    vi.spyOn(api, "patch").mockRejectedValue(new ApiError(409, "Источник изменён другим участником"));
    render(wrap(<ProjectGitSources projectId={7} canEdit />));
    await screen.findByRole("heading", { name: "Frontend" });
    fireEvent.click(screen.getByRole("button", { name: "Настроить" }));
    fireEvent.change(screen.getByLabelText("Ветка или ref"), { target: { value: "my-draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить источник" }));
    await screen.findByText("Источник изменён другим участником");
    expect((screen.getByLabelText("Ветка или ref") as HTMLInputElement).value).toBe("my-draft");
    fireEvent.click(screen.getByRole("button", { name: "Загрузить актуальные настройки" }));
    await waitFor(() => expect(screen.queryByLabelText("Ветка или ref")).toBeNull());
});

it("не показывает формы credentials участнику и хранит SHA каждого подключения отдельно", async () => {
    vi.spyOn(api, "get").mockResolvedValue({ items: [connection(2, "Auth", "release")], total: 1, offset: 0, limit: 20, next_offset: null });
    render(wrap(<ProjectGitSources projectId={7} canEdit={false} />));
    await screen.findByRole("heading", { name: "Auth" });
    expect(screen.getByLabelText("a".repeat(40))).toBeTruthy();
    expect(screen.getByLabelText("b".repeat(40))).toBeTruthy();
    expect(screen.getAllByText("Неполный охват").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Изменить доступ" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Добавить репозитории" })).toBeNull();
});

it("показывает срок паузы существующего источника и блокирует ручную синхронизацию", async () => {
    const nextPoll = new Date(Date.now() + 60_000).toISOString();
    const paused = { ...connection(1, "Frontend", "main"), last_error_code: "rate_limited", next_poll_at: nextPoll };
    vi.spyOn(api, "get").mockResolvedValue({ items: [paused], total: 1, next_offset: null });
    render(wrap(<ProjectGitSources projectId={7} canEdit />));
    const message = await screen.findByText(/Следующая проверка после/);
    expect(message.querySelector("time")?.dateTime).toBe(nextPoll);
    expect((screen.getByRole("button", { name: "Повторить проверку" }) as HTMLButtonElement).disabled).toBe(true);
});

it("сохраняет выбранные стадии по ID и выключает правило пустым выбором", async () => {
    const item = { ...connection(1, "Frontend", "main"), sync_stage_ids: [30, 99] };
    const stages = [{ id: 20, name: "Проверка качества" }, { id: 30, name: "Выпущено" }];
    vi.spyOn(api, "get").mockImplementation(async path => path.endsWith("/stages") ? stages : { items: [item], total: 1, next_offset: null });
    const patch = vi.spyOn(api, "patch").mockImplementation(async (_, body) => { Object.assign(item, body); return item; });
    render(wrap(<ProjectGitSources projectId={7} canEdit />));
    fireEvent.click(await screen.findByRole("button", { name: "Настроить" }));
    expect((await screen.findByLabelText("Выпущено") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText("Удалённые стадии будут исключены из правила при сохранении.")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Проверка качества"));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить источник" }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith("/api/v1/projects/7/git/connections/1", { sync_stage_ids: [30, 20], expected_revision: 3 }));
    await waitFor(() => expect(screen.queryByLabelText("Проверка качества")).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Настроить" }));
    fireEvent.click(await screen.findByLabelText("Проверка качества"));
    fireEvent.click(screen.getByLabelText("Выпущено"));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить источник" }));
    await waitFor(() => expect(patch).toHaveBeenLastCalledWith(expect.any(String), expect.objectContaining({ sync_stage_ids: [] })));
});

it("участнику недоступны настройка стадий и секрет webhook", async () => {
    vi.spyOn(api, "get").mockResolvedValue({ items: [connection(1, "Frontend", "main")], total: 1, next_offset: null });
    render(wrap(<ProjectGitSources projectId={7} canEdit={false} />));
    await screen.findByRole("heading", { name: "Frontend" });
    expect(screen.queryByRole("button", { name: "Настроить" })).toBeNull();
    expect(screen.queryByLabelText("Новый секрет webhook")).toBeNull();
});

it("settings manage connections without requesting tree, source, search or maps", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({ items: [connection(1, "Frontend", "main")], total: 1, next_offset: null });
    render(wrap(<ProjectGitSources projectId={7} canEdit collapsible />));
    fireEvent.click(await screen.findByText("Управлять репозиториями"));
    await screen.findByRole("heading", { name: "Frontend" });
    expect(screen.getByRole("link", { name: "Исследовать код" }).getAttribute("href")).toContain("view=code&connection=1");
    expect(get.mock.calls.every(([path]) => !/\/(tree|file|search|map|graph)[?/]?/.test(path))).toBe(true);
});
