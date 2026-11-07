// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import type { TaskAttachment, TaskComment } from "@/lib/types";
import { TaskCommentsSection } from "./TaskCommentsSection";

const clients: QueryClient[] = [];
const attachment: TaskAttachment = { id: 9, task_id: 5, original_name: "План.pdf", content_type: "application/pdf", size: 100, created_at: "2026-11-07T10:00:00Z", content_url: "/api/v1/tasks/5/attachments/9/content", previewable: false, caption: "Этапы" };
function setup(comments: TaskComment[] = [], attachments: TaskAttachment[] = [attachment]) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    clients.push(client);
    const onChange = vi.fn();
    const view = render(<QueryClientProvider client={client}><TaskCommentsSection taskId={5} comments={comments} attachments={attachments} currentUserId={1} onChange={onChange} onReport={vi.fn()} loading={false} /></QueryClientProvider>);
    return { client, onChange, user: userEvent.setup(), ...view };
}
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); });

it("сохраняет текст, подписи и файлы при ошибке и повторяет один UUID; затем очищает черновик", async () => {
    const requests: FormData[] = [];
    let release: (() => void) | undefined;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
        requests.push(init!.body as FormData);
        if (requests.length === 1) return new Response(JSON.stringify({ detail: "Связь прервалась" }), { status: 503 });
        await new Promise<void>(resolve => { release = resolve; });
        return new Response(JSON.stringify({ id: 10, task_id: 5, body_md: "Контекст", attachments: [attachment] }), { status: 201 });
    });
    const { user, onChange, client } = setup();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await user.type(screen.getByLabelText("Текст комментария"), "Контекст");
    await user.upload(screen.getByLabelText("Файлы к комментарию", { selector: "input" }), new File(["rows"], "Бюджет.xlsx", { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }));
    await user.type(screen.getByLabelText("Подпись к Бюджет.xlsx"), "Версия для встречи");
    await user.click(screen.getByText("Из файлов задачи"));
    await user.click(screen.getByRole("checkbox", { name: "Прикрепить План.pdf" }));
    await user.click(screen.getByRole("button", { name: "Отправить" }));
    await screen.findByText(/Связь прервалась/);
    expect((screen.getByLabelText("Текст комментария") as HTMLTextAreaElement).value).toBe("Контекст");
    expect((screen.getByLabelText("Подпись к Бюджет.xlsx") as HTMLInputElement).value).toBe("Версия для встречи");
    await user.click(screen.getByRole("button", { name: "Отправить" }));
    await waitFor(() => expect(requests).toHaveLength(2));
    expect((screen.getByLabelText("Текст комментария") as HTMLTextAreaElement).disabled).toBe(true);
    expect((screen.getByLabelText("Подпись к Бюджет.xlsx") as HTMLInputElement).disabled).toBe(true);
    expect(requests[0].get("request_id")).toBe(requests[1].get("request_id"));
    expect(requests[0].getAll("files")).toHaveLength(1);
    expect(requests[0].get("captions")).toBe('["Версия для встречи"]');
    expect(requests[0].get("attachment_ids")).toBe("[9]");
    release!();
    await waitFor(() => expect((screen.getByLabelText("Текст комментария") as HTMLTextAreaElement).value).toBe(""));
    expect(screen.queryByLabelText("Подпись к Бюджет.xlsx")).toBeNull();
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(invalidate.mock.calls.map(([filter]) => filter?.queryKey)).toEqual(expect.arrayContaining([["tasks", 5, "comments"], ["tasks", 5, "attachments"], ["tasks", 5, "files-search"]]));
});

it("новое содержимое после ошибки получает новый UUID; можно отправить только существующий файл", async () => {
    const requests: FormData[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
        requests.push(init!.body as FormData);
        return new Response(JSON.stringify({ detail: "Временно недоступно" }), { status: 503 });
    });
    const { user } = setup();
    await user.click(screen.getByText("Из файлов задачи"));
    await user.click(screen.getByRole("checkbox", { name: "Прикрепить План.pdf" }));
    await user.click(screen.getByRole("button", { name: "Отправить" }));
    await screen.findByText(/Временно недоступно/);
    expect(requests[0].get("body_md")).toBe("");
    fireEvent.change(screen.getByLabelText("Текст комментария"), { target: { value: "Добавил пояснение" } });
    await user.click(screen.getByRole("button", { name: "Отправить" }));
    await waitFor(() => expect(requests).toHaveLength(2));
    expect(requests[1].get("request_id")).not.toBe(requests[0].get("request_id"));
});

it("показывает карточку вложения под комментарием и удаляет только комментарий", async () => {
    const calls: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation(async input => { calls.push(String(input)); return new Response(null, { status: 204 }); });
    const comment: TaskComment = { id: 12, task_id: 5, author_name: "Иван", body_md: "", created_at: attachment.created_at, attachments: [attachment] };
    const { user, onChange } = setup([comment]);
    expect(screen.getAllByText("План.pdf")).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Удалить" }));
    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    expect(calls).toHaveLength(1);
    expect(calls[0]).toContain("/comments/12");
});

it("карточка под комментарием использует актуальную подпись и состояние файла задачи", () => {
    const old = { ...attachment, caption: "Старая подпись", extraction_status: "pending" as const };
    const comment: TaskComment = { id: 12, task_id: 5, author_name: "Иван", body_md: "Материалы", created_at: attachment.created_at, attachments: [old] };
    setup([comment], [{ ...attachment, caption: "Уточнённая подпись", extraction_status: "ready", fts_status: "ready", index_status: "disabled" }]);
    const article = document.querySelector("article")!;
    expect(article.textContent).toContain("Уточнённая подпись");
    expect(article.textContent).toContain("Текст доступен для поиска");
    expect(article.textContent).not.toContain("Готовим текст");
    expect(article.textContent).not.toContain("Старая подпись");
});
