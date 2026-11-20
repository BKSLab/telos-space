// @vitest-environment jsdom
import { render, screen, waitFor, cleanup, fireEvent, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { TaskReportSection } from "./TaskReportSection";
import { api } from "@/lib/api";
import type { TaskComment } from "@/lib/types";
vi.mock("@/lib/api", () => ({ api: { get: vi.fn(), post: vi.fn() } }));
const report = { id: 12, task_id: 5, revision: 1, body_md: "Первый результат", author_name: "Кирилл", created_at: null, source_comment_id: null, source_comment_original_id: null, source_author_name: null };
it("переход из истории показывает выбранную публикацию отдельно от текущего отчёта", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    vi.mocked(api.get).mockImplementation(async path => path.includes("/history") ? [report] : { ...report, id: 13, revision: 2, body_md: "Текущий результат" });
    render(<QueryClientProvider client={client}><TaskReportSection taskId={5} active selectedComment={null} clearComment={() => {}} targetReportId={12} targetRevision={1} /></QueryClientProvider>);
    await screen.findByText("Версия 1 из истории");
    expect(within(screen.getByRole("region", { name: "Отчёт из истории" })).getByText("Первый результат")).toBeTruthy();
    expect(await screen.findByText("Текущий результат")).toBeTruthy();
    expect(api.post).not.toHaveBeenCalled();
    expect(api.get).toHaveBeenCalledWith("/api/v1/tasks/5/report/history?report_id=12");
    client.clear();
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });
function mount(selectedComment: TaskComment | null = null, canEdit = true, initial = report) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    vi.mocked(api.get).mockResolvedValue(initial);
    vi.mocked(api.post).mockResolvedValue({ ...report, revision: 2 });
    const clearComment = vi.fn();
    const view = render(<QueryClientProvider client={client}><TaskReportSection taskId={5} active canEdit={canEdit} selectedComment={selectedComment} clearComment={clearComment} /></QueryClientProvider>);
    return { client, clearComment, ...view };
}
it("показывает текущий отчёт сразу, редактор открывается по кнопке", async () => {
    mount();
    await screen.findByText(report.body_md);
    expect(screen.queryByRole("textbox")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Изменить отчёт" }));
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(report.body_md);
    fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(api.post).not.toHaveBeenCalled();
}, 15_000);
it("не ребазирует черновик молча при фоновом обновлении отчёта", async () => {
    const { client } = mount();
    fireEvent.click(await screen.findByRole("button", { name: "Изменить отчёт" }));
    const field = screen.getByRole("textbox", { name: "Отчёт по задаче" });
    fireEvent.change(field, { target: { value: "Мой черновик" } });
    client.setQueryData(["tasks", 5, "report"], { ...report, revision: 3, body_md: "Чужая правка" });
    await waitFor(() => expect((field as HTMLTextAreaElement).value).toBe("Мой черновик"));
    fireEvent.click(screen.getByRole("button", { name: "Опубликовать отчёт" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/v1/tasks/5/report", { body_md: "Мой черновик", expected_revision: 1 }));
});
it("после публикации комментария показывает новый отчёт и закрывает форму", async () => {
    const comment = { id: 7, task_id: 5, body_md: "Комментарий-результат", author_name: "Автор" } as TaskComment;
    const { client, clearComment, rerender } = mount(comment);
    vi.mocked(api.post).mockResolvedValue({ ...report, revision: 2, body_md: comment.body_md });
    fireEvent.click(await screen.findByRole("button", { name: "Опубликовать комментарий как отчёт" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/v1/tasks/5/report/from-comment", { comment_id: 7, expected_revision: 1, expected_comment_body_md: comment.body_md }));
    await waitFor(() => expect(clearComment).toHaveBeenCalledOnce());
    // Родитель снимает выбранный комментарий после успешного сохранения.
    rerender(<QueryClientProvider client={client}><TaskReportSection taskId={5} active selectedComment={null} clearComment={clearComment} /></QueryClientProvider>);
    await screen.findByText(comment.body_md);
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Опубликовать комментарий как отчёт" })).toBeNull();
});
it("публикация ручного отчёта закрывает редактор и показывает сохранённый текст", async () => {
    mount(null, true, { ...report, revision: 0, body_md: "" });
    fireEvent.click(await screen.findByRole("button", { name: "Добавить отчёт" }));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Новый результат" } });
    vi.mocked(api.post).mockResolvedValue({ ...report, body_md: "Новый результат" });
    fireEvent.click(screen.getByRole("button", { name: "Опубликовать отчёт" }));
    await screen.findByText("Новый результат");
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.getByRole("button", { name: "Изменить отчёт" })).toBeTruthy();
});
it("читатель видит отчёт без кнопок публикации и поля ввода", async () => {
    mount(null, false);
    await screen.findByText(report.body_md);
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: /отчёт/ })).toBeNull();
});
it("публикация сообщает занятость до ответа и снимает её после сохранения", async () => {
    const busy = vi.fn();
    const client = new QueryClient();
    vi.mocked(api.get).mockResolvedValue(report);
    let finish!: (value: typeof report) => void;
    vi.mocked(api.post).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    render(<QueryClientProvider client={client}><TaskReportSection taskId={5} active selectedComment={null} clearComment={() => {}} onPublishingChange={busy} /></QueryClientProvider>);
    fireEvent.click(await screen.findByRole("button", { name: "Изменить отчёт" }));
    const field = screen.getByRole("textbox");
    fireEvent.change(field, { target: { value: "Результат" } });
    fireEvent.click(screen.getByRole("button", { name: "Опубликовать отчёт" }));
    await waitFor(() => expect(busy).toHaveBeenCalledWith(true));
    expect((field as HTMLTextAreaElement).disabled).toBe(true);
    finish({ ...report, revision: 2 });
    await waitFor(() => expect(busy).toHaveBeenLastCalledWith(false));
});

it("читателю доступен текст и история без действий публикации", async () => {
    const client = new QueryClient();
    vi.mocked(api.get).mockResolvedValue(report);
    render(<QueryClientProvider client={client}><TaskReportSection taskId={5} active canEdit={false} selectedComment={null} clearComment={() => {}} /></QueryClientProvider>);
    await screen.findByText(report.body_md);
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Опубликовать отчёт" })).toBeNull();
    expect(screen.getByText("История публикаций")).toBeTruthy();
    expect(api.post).not.toHaveBeenCalled();
});
