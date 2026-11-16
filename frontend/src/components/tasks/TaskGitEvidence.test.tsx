// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { GitTaskLink } from "@/lib/projectKnowledge";
import { TaskGitEvidence } from "./TaskGitEvidence";

const clients: QueryClient[] = [];
function wrap(children: React.ReactNode) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } });
    clients.push(client);
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); });

it("без Git сохраняет возможность отчёта и ничего не привязывает автоматически", async () => {
    vi.spyOn(api, "get").mockResolvedValue({ project_id: 7, task_id: 12, items: [], partial: false });
    const write = vi.spyOn(api, "post");
    render(wrap(<TaskGitEvidence projectId={7} taskId={12} />));
    await screen.findByText("Git-подтверждений нет. Результат задачи можно зафиксировать в отчёте.");
    expect(write).not.toHaveBeenCalled();
});

it("показывает разные SHA и среды двух репозиториев без общего вывода о проде", async () => {
    const link: GitTaskLink = { id: 1, task_id: 12, change_id: 1, connection_id: 1, repository_id: 101, kind: "pull_request", title: "Frontend PR", source_url: "https://github.com/example/frontend/pull/1", commit_sha: "a".repeat(40), merge_sha: "b".repeat(40), pull_number: 1, status: "merged", revision: 1, checked_at: "2026-11-16T12:00:00Z", evidence_partial: false, last_error_code: null, evidence: [{ kind: "check", provider_type: "check_run", provider_id: "1", connection_id: 1, repository_id: 101, commit_sha: "a".repeat(40), status: "completed", conclusion: "success", environment: null, source_url: "https://github.com/example/frontend/actions/runs/1", observed_at: "2026-11-16T12:00:00Z", upstream_at: null, is_current: null }] };
    const second: GitTaskLink = { ...link, id: 2, change_id: 2, connection_id: 2, repository_id: 102, title: "Auth PR", evidence_partial: true, evidence: [{ ...link.evidence[0], kind: "deployment", provider_type: "deployment", provider_id: "2", connection_id: 2, repository_id: 102, commit_sha: "c".repeat(40), status: "inactive", conclusion: null, environment: "production", is_current: false }] };
    vi.spyOn(api, "get").mockResolvedValue({ project_id: 7, task_id: 12, items: [link, second], partial: true });
    const post = vi.spyOn(api, "post").mockResolvedValue(second);
    render(wrap(<TaskGitEvidence projectId={7} taskId={12} />));
    await screen.findByRole("link", { name: "Frontend PR" });
    expect(screen.getByRole("link", { name: "Auth PR" })).toBeTruthy();
    expect(screen.getByText(/Проверка CI · Завершено · Успех/)).toBeTruthy();
    expect(screen.getByText(/Выкладка · production · Неактивно/)).toBeTruthy();
    expect(screen.getByText("Эта выкладка больше не является текущей.")).toBeTruthy();
    expect(screen.getByText("c".repeat(40))).toBeTruthy();
    expect(screen.queryByText("Подтверждена текущая успешная выкладка в этой среде.")).toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: "Проверить подтверждения" })[1]);
    await waitFor(() => expect(post).toHaveBeenCalledWith("/api/v1/projects/7/git/tasks/12/links/2/refresh"));
});

it("различает одноимённые PR двух репозиториев и коммит до явной записи выбранной связи", async () => {
    const candidates = [
        { connection_id: 1, repository_id: 101, kind: "pull_request", pull_number: 12, commit_sha: "a".repeat(40), title: "DEMO-7 Fix authentication", source_url: "https://github.com/example/frontend/pull/12", ambiguous: false },
        { connection_id: 2, repository_id: 202, kind: "pull_request", pull_number: 44, commit_sha: "b".repeat(40), title: "DEMO-7 Fix authentication", source_url: "https://github.com/example/auth/pull/44", ambiguous: false },
        { connection_id: 2, repository_id: 202, kind: "commit", pull_number: null, commit_sha: "c".repeat(40), title: "DEMO-7 Update contract", source_url: "https://github.com/example/auth/commit/" + "c".repeat(40), ambiguous: false },
    ];
    vi.spyOn(api, "get").mockImplementation(async path => path.endsWith("/candidates")
        ? { items: candidates, partial: false }
        : { project_id: 7, task_id: 12, items: [], partial: false });
    const post = vi.spyOn(api, "post").mockResolvedValue({});
    render(wrap(<TaskGitEvidence projectId={7} taskId={12} />));
    fireEvent.click(screen.getByText("Найти возможные связи по ключу задачи"));
    await screen.findByRole("button", { name: "Привязать PR #12 из репозитория 101" });
    const chosen = screen.getByRole("button", { name: "Привязать PR #44 из репозитория 202" });
    expect(screen.getByText("Репозиторий 101 · PR #12")).toBeTruthy();
    expect(screen.getByText("Репозиторий 202 · PR #44")).toBeTruthy();
    expect(screen.getByRole("button", { name: `Привязать коммит ${candidates[2].commit_sha} из репозитория 202` })).toBeTruthy();
    for (const candidate of candidates) expect(screen.getByRole("link", { name: candidate.source_url }).getAttribute("href")).toBe(candidate.source_url);
    expect(post).not.toHaveBeenCalled();
    fireEvent.click(chosen);
    await waitFor(() => expect(post).toHaveBeenCalledExactlyOnceWith("/api/v1/projects/7/git/tasks/12/links", { connection_id: 2, kind: "pull_request", pull_number: 44 }));
});
