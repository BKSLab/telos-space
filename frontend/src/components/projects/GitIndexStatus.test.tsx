// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { GitConnection } from "@/lib/projectKnowledge";
import { GitIndexBadge, GitIndexDetails, GitIndexSummary } from "./GitIndexStatus";

const ready: GitConnection = {
    id: 1, project_id: 1, provider: "github", repository_id: 100,
    owner: "example", repo: "frontend", name: "Frontend", purpose: "", ref: "main",
    include_paths: [], exclude_patterns: [], enabled: true, is_private: false,
    share_private_with_project: false, has_credential: true, has_webhook_secret: false,
    revision: 1, generation: 1, credential_revision: 1, state: "ready",
    observed_sha: "a".repeat(40), indexed_sha: "a".repeat(40),
    observed_at: null, indexed_at: "2026-11-20T12:00:00Z", checked_at: "2026-11-20T12:05:00Z",
    last_error_code: null,
    coverage: { total_files: 20, indexed_files: 20, excluded_files: 0, failed_files: 0, truncated: false },
};

afterEach(cleanup);

it.each([
    [{}, "Индекс актуален"],
    [{ state: "pending" }, "В очереди"],
    [{ state: "syncing" }, "Индексируется"],
    [{ state: "partial" }, "Неполный охват"],
    [{ state: "error" }, "Ошибка индексации"],
    [{ state: "access_lost" }, "Доступ потерян"],
    [{ enabled: false, last_error_code: "rate_limited" }, "Отключён"],
    [{ last_error_code: "rate_limited" }, "Индексация приостановлена"],
    [{ last_error_code: "github_profile_pending" }, "Ожидает проверки доступа"],
    [{ state: "access_lost", last_error_code: "github_profile_pending" }, "Ожидает проверки доступа"],
    [{ indexed_sha: "b".repeat(40) }, "Есть новая версия"],
    [{ indexed_sha: null }, "Версия не подтверждена"],
    [{ coverage: { ...ready.coverage, truncated: true } }, "Неполный охват"],
    [{ coverage: { ...ready.coverage, failed_files: 1 } }, "Неполный охват"],
    [{ coverage: { ...ready.coverage, excluded_files: 1 } }, "Неполный охват"],
] satisfies [Partial<GitConnection>, string][])('различает реальное состояние %j', (patch, label) => {
    render(<GitIndexBadge connection={{ ...ready, ...patch }} />);
    expect(screen.getByText(label)).toBeTruthy();
});

it("очередь со старым полным охватом не выдаёт процент выполнения или актуальный поиск", () => {
    render(<GitIndexDetails connection={{ ...ready, state: "pending", observed_sha: "b".repeat(40) }} />);
    expect(screen.getByText("Предыдущая версия")).toBeTruthy();
    expect(screen.getByText("Ожидает результата обновления")).toBeTruthy();
    expect(screen.getByText(/Это охват файлов, а не процент выполнения/)).toBeTruthy();
    expect(screen.queryByText("Актуальная версия")).toBeNull();
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.queryByText(/100%/)).toBeNull();
    expect(screen.getByLabelText("a".repeat(40)).textContent).toBe("aaaaaaaa");
});

it("показывает отсутствие метрик без вымышленных файлов и сохраняет неполноту", () => {
    render(<GitIndexDetails connection={{ ...ready, state: "syncing", indexed_sha: null, coverage: { total_files: 0, indexed_files: 0, excluded_files: 0, failed_files: 0, truncated: true } }} />);
    expect(screen.getByText("Данные об охвате появятся после обработки файлов.")).toBeTruthy();
    expect(screen.getByText("Охват ограничен. Данные не отражают весь репозиторий.")).toBeTruthy();
    expect(screen.queryByRole("img")).toBeNull();
});

it.each([{ enabled: false }, { state: "access_lost" }])("не обещает поиск при отключении или утрате доступа", patch => {
    render(<GitIndexDetails connection={{ ...ready, ...patch }} />);
    expect(screen.getByText("Недоступен")).toBeTruthy();
    expect(screen.queryByText("Актуальная версия")).toBeNull();
});

it("ограничивает сводку текущей страницей и не считает partial готовым", () => {
    render(<GitIndexSummary page={{ items: [ready, { ...ready, id: 2, state: "partial" }, { ...ready, id: 3, state: "pending" }], total: 23, offset: 0, limit: 20, next_offset: 20 }} />);
    expect(screen.getByText("На странице: 3 из 23")).toBeTruthy();
    expect(screen.getByText("Актуальны: 1")).toBeTruthy();
    expect(screen.getByText("Требуют внимания: 1")).toBeTruthy();
    expect(screen.getByText("В работе: 1")).toBeTruthy();
});
