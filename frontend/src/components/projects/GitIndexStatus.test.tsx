// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { GitConnection } from "@/lib/projectKnowledge";
import { GitIndexBadge, GitIndexDetails, GitIndexSummary } from "./GitIndexStatus";

const ready: GitConnection = {
    id: 1, project_id: 1, provider: "github", repository_id: 100,
    owner: "example", repo: "frontend", name: "Frontend", purpose: "", ref: "main",
    enabled: true, is_private: false,
    share_private_with_project: false, has_credential: true, has_webhook_secret: false,
    revision: 1, generation: 1, credential_revision: 1, state: "ready",
    observed_sha: "a".repeat(40), indexed_sha: "a".repeat(40),
    observed_at: null, indexed_at: "2026-11-20T12:00:00Z", checked_at: "2026-11-20T12:05:00Z",
    last_error_code: null,
    coverage: { total_files: 20, cataloged_files: 20, indexed_files: 20, metadata_files: 0, metadata_counts: {}, failed_files: 0, truncated: false },
};

afterEach(cleanup);

it.each([
    [{}, "Индекс актуален"],
    [{ state: "pending" }, "В очереди"],
    [{ state: "syncing" }, "Индексируется"],
    [{ sync_pending: true }, "В очереди на проверку"],
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
    [{ coverage: { ...ready.coverage, indexed_files: 19, metadata_files: 1, metadata_counts: { binary_file: 1 } } }, "Индекс актуален"],
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
    render(<GitIndexDetails connection={{ ...ready, state: "syncing", indexed_sha: null, coverage: { total_files: 0, cataloged_files: 0, indexed_files: 0, metadata_files: 0, metadata_counts: {}, failed_files: 0, truncated: true } }} />);
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

it.each([
    { state: "partial", failed_files: 1, truncated: false },
    { state: "pending", failed_files: 1, truncated: false },
    { state: "partial", failed_files: 0, truncated: true },
])("не скрывает незавершённый результат за ожидающим повтором: %j", result => {
    const retry: GitConnection = {
        ...ready, state: result.state, indexed_sha: null, sync_pending: true,
        last_error_code: null, checked_at: "2026-11-20T12:00:00Z", next_poll_at: "2026-11-20T13:00:00Z",
        coverage: { total_files: 1, cataloged_files: 1, indexed_files: 0, metadata_files: 0, metadata_counts: {}, failed_files: result.failed_files, truncated: result.truncated },
    };
    const metadataOnly = { ...ready, id: 2, coverage: { ...ready.coverage, indexed_files: 0, metadata_files: 20, metadata_counts: { binary_file: 20 } } };
    render(<><GitIndexBadge connection={retry} /><GitIndexSummary page={{ items: [retry, metadataOnly], total: 2, offset: 0, limit: 20, next_offset: null }} /></>);
    expect(screen.getByText("Неполный охват")).toBeTruthy();
    expect(screen.getByText("Требуют внимания: 1")).toBeTruthy();
    expect(screen.getByText("Актуальны: 1")).toBeTruthy();
    expect(screen.queryByText("В очереди на проверку")).toBeNull();
    expect(screen.queryByText("В очереди")).toBeNull();
    expect(screen.queryByText(/В работе:/)).toBeNull();
});

it("различает сохранённый секрет, ping и успешную сверку события", () => {
    const delivery = "2026-11-20T12:06:00Z";
    const view = render(<GitIndexDetails connection={{ ...ready, has_webhook_secret: true, last_webhook_received_at: delivery, last_webhook_event: "ping", last_webhook_processed_at: null, poll_interval_seconds: 3600, last_sync_reason: "task_stage", last_sync_requested_at: delivery }} />);
    expect(screen.getByText("Раз в час")).toBeTruthy();
    expect(screen.getByText("Переход задачи")).toBeTruthy();
    expect(screen.getByText(/Проверка связи \(ping\)/)).toBeTruthy();
    expect(screen.getByText(/Успешная сверка события ещё не подтверждена/)).toBeTruthy();
    expect(screen.queryByText("Последняя успешная сверка события")).toBeNull();
    view.rerender(<GitIndexDetails connection={{ ...ready, has_webhook_secret: true, last_webhook_received_at: delivery, last_webhook_event: "push", last_webhook_processed_at: "2026-11-20T12:07:00Z" }} />);
    expect(screen.getByText("Последняя успешная сверка события")).toBeTruthy();
    expect(screen.queryByText(/Успешная сверка события ещё не подтверждена/)).toBeNull();
});

it("различает текстовый индекс и учёт метаданных, не обещая чтение двоичного содержимого", () => {
    render(<GitIndexDetails connection={{ ...ready, coverage: { ...ready.coverage, indexed_files: 17, metadata_files: 3, metadata_counts: { binary_file: 2, submodule: 1 } } }} />);
    expect(screen.getByText("Почему только метаданные")).toBeTruthy();
    expect(screen.getByText("Двоичные файлы")).toBeTruthy();
    expect(screen.getByText("Подмодули Git")).toBeTruthy();
    expect(screen.getByText("Текст: 17")).toBeTruthy();
    expect(screen.getByText("Только метаданные: 3")).toBeTruthy();
    expect(screen.getByText(/не означает, что Арис прочитал содержимое/)).toBeTruthy();
    expect(screen.getByText("Ошибок: 0")).toBeTruthy();
    expect(screen.getByRole("img", { name: "Охват файлов: текст 17, только метаданные 3, ошибок 0, в каталоге 20, всего 20" })).toBeTruthy();
});

it("не скрывает ошибку чтения за присутствием файла в каталоге", () => {
    render(<GitIndexDetails connection={{ ...ready, state: "partial", coverage: { ...ready.coverage, indexed_files: 19, failed_files: 1 } }} />);
    expect(screen.getByText("Ошибок: 1")).toBeTruthy();
    expect(screen.getByText(/Часть файлов не удалось прочитать/)).toBeTruthy();
    expect(screen.getByRole("img", { name: "Охват файлов: текст 19, только метаданные 0, ошибок 1, в каталоге 20, всего 20" })).toBeTruthy();
});

it("каталог из двоичных файлов не обещает наличие текстового поиска", () => {
    render(<GitIndexDetails connection={{ ...ready, coverage: { ...ready.coverage, indexed_files: 0, metadata_files: 20, metadata_counts: { binary_file: 20 } } }} />);
    expect(screen.getByText("Только каталог файлов")).toBeTruthy();
    expect(screen.getByText("Текст: 0")).toBeTruthy();
    expect(screen.queryByText("Актуальная версия")).toBeNull();
});
