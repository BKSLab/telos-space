// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { GitConnection, GitTreeEntry, GitTreePage } from "@/lib/projectKnowledge";
import { GitFileCatalog } from "./GitFileCatalog";

const connection = { id: 2, project_id: 7, enabled: true, state: "ready", observed_sha: "a".repeat(40) } as GitConnection;
const entry = (path: string, index_state: GitTreeEntry["index_state"], index_reason: string | null = null): GitTreeEntry => ({ path, sha: "b".repeat(40), type: "blob", mode: "100644", size: 1024, index_state, index_reason });
const page: GitTreePage = { connection_id: 2, repository_id: 102, commit_sha: "a".repeat(40), items: [entry(".env.example", "ready"), entry("package-lock.json", "ready"), entry("assets/photo.png", "metadata", "binary_file"), entry("huge.log", "error", "transport_limit"), entry("scripts/new", "pending")], offset: 0, limit: 30, next_offset: null, truncated: false, stale: false, total_files: 5 };
const clients: QueryClient[] = [];
function mount(current = connection) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); clients.push(client);
    const onRead = vi.fn();
    render(<QueryClientProvider client={client}><GitFileCatalog connection={current} onRead={onRead} /></QueryClientProvider>);
    return onRead;
}
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); });

it("читает каталог по раскрытию и показывает все типы без скрытия имён", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue(page);
    const onRead = mount();
    expect(get).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Каталог файлов"));
    await screen.findByText(".env.example");
    expect(screen.getByText("package-lock.json")).toBeTruthy();
    const binary = screen.getByText("assets/photo.png").closest("li")!;
    expect(within(binary).getByText("Только метаданные")).toBeTruthy();
    expect(within(binary).queryByRole("button")).toBeNull();
    const failed = screen.getByText("huge.log").closest("li")!;
    expect(within(failed).getByText("Ошибка чтения")).toBeTruthy();
    expect(within(failed).queryByRole("button")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Читать .env.example" }));
    expect(onRead).toHaveBeenCalledWith({ connection_id: 2, commit_sha: "a".repeat(40), path: ".env.example" });
    expect(get).toHaveBeenCalledTimes(1);
});

it("переходит по страницам, ищет путь без изменения состава индекса и сбрасывает отбор", async () => {
    const get = vi.spyOn(api, "get").mockImplementation(async path => path.includes("prefix=docs") ? { ...page, total_files: 0, items: [] } : path.includes("offset=30") ? { ...page, offset: 30, items: [entry("tail.txt", "ready")] } : { ...page, total_files: 31, next_offset: 30 });
    mount(); fireEvent.click(screen.getByText("Каталог файлов"));
    fireEvent.click(await screen.findByRole("button", { name: "Дальше" }));
    await screen.findByText("tail.txt");
    fireEvent.change(screen.getByLabelText("Файл или папка"), { target: { value: "docs/" } });
    fireEvent.click(screen.getByRole("button", { name: "Показать" }));
    await screen.findByText("Файлы по этому пути не найдены в доступном каталоге.");
    expect(get).toHaveBeenLastCalledWith(expect.stringContaining("offset=0&limit=30&prefix=docs"));
    fireEvent.click(screen.getByRole("button", { name: "Весь каталог" }));
    await screen.findByText(".env.example");
    expect(screen.getByText(/только отображение каталога, а не состав индекса/)).toBeTruthy();
});

it("ошибка запроса не подменяется пустым каталогом и может быть повторена", async () => {
    const get = vi.spyOn(api, "get").mockRejectedValueOnce(new Error("GitHub недоступен")).mockResolvedValue(page);
    mount(); fireEvent.click(screen.getByText("Каталог файлов"));
    await screen.findByText("GitHub недоступен");
    expect(screen.queryByText("Файлы по этому пути не найдены в доступном каталоге.")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Обновить каталог" }));
    await screen.findByText(".env.example");
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
});

it.each([{ enabled: false }, { state: "access_lost" }])("не читает каталог отключённого или недоступного подключения", patch => {
    const get = vi.spyOn(api, "get"); mount({ ...connection, ...patch });
    expect(screen.queryByText("Каталог файлов")).toBeNull();
    expect(get).not.toHaveBeenCalled();
});
