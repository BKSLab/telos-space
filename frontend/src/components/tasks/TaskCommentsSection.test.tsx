// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { documentFixture, documentPageFixture } from "@/components/documents/documentFixtures.test-utils";
import type { TaskComment } from "@/lib/types";
import { TaskCommentsSection } from "./TaskCommentsSection";

const clients: QueryClient[] = [];
const document = documentFixture({ title: "План проекта", caption: "Уточнённая подпись" });
function setup(comments: TaskComment[] = []) {
    vi.spyOn(api, "get").mockImplementation(async path => path.includes("?") ? documentPageFixture([document]) : document);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    clients.push(client);
    const onChange = vi.fn();
    const view = render(<QueryClientProvider client={client}><TaskCommentsSection taskId={5} comments={comments} currentUserId={1} onChange={onChange} onReport={vi.fn()} loading={false} /></QueryClientProvider>);
    return { client, onChange, user: userEvent.setup(), ...view };
}
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); });

it("сохраняет текст, подписи и ссылки при ошибке и повторяет UUID; затем очищает черновик", async () => {
    const requests: FormData[] = []; let release: (() => void) | undefined;
    vi.spyOn(api, "postForm").mockImplementation(async (_path, body) => {
        requests.push(body);
        if (requests.length === 1) throw new Error("Связь прервалась");
        await new Promise<void>(resolve => { release = resolve; });
        return { id: 10, task_id: 5, body_md: "Контекст", document_refs: [document.ref] };
    });
    const { user, onChange, client } = setup(); const invalidate = vi.spyOn(client, "invalidateQueries");
    await user.type(screen.getByLabelText("Текст комментария"), "Контекст");
    await user.upload(screen.getByLabelText("Документы к комментарию", { selector: "input" }), new File(["rows"], "Бюджет.xlsx", { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }));
    await user.type(screen.getByLabelText("Подпись к Бюджет.xlsx"), "Версия для встречи");
    await user.click(screen.getByText("Из документов задачи"));
    await user.click(await screen.findByRole("checkbox", { name: "Прикрепить План проекта" }));
    await user.click(screen.getByRole("button", { name: "Отправить" }));
    await screen.findByText(/Связь прервалась/);
    expect((screen.getByLabelText("Текст комментария") as HTMLTextAreaElement).value).toBe("Контекст");
    expect((screen.getByLabelText("Подпись к Бюджет.xlsx") as HTMLInputElement).value).toBe("Версия для встречи");
    await user.click(screen.getByRole("button", { name: "Отправить" }));
    await waitFor(() => expect(requests).toHaveLength(2));
    expect((screen.getByLabelText("Текст комментария") as HTMLTextAreaElement).disabled).toBe(true);
    expect(requests[0].get("request_id")).toBe(requests[1].get("request_id"));
    expect(requests[0].getAll("files")).toHaveLength(1);
    expect(requests[0].get("captions")).toBe('["Версия для встречи"]');
    expect(requests[0].get("document_refs")).toBe(JSON.stringify([document.ref]));
    expect(requests[0].has("attachment_ids")).toBe(false);
    release!();
    await waitFor(() => expect((screen.getByLabelText("Текст комментария") as HTMLTextAreaElement).value).toBe(""));
    expect(screen.queryByLabelText("Подпись к Бюджет.xlsx")).toBeNull();
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(invalidate.mock.calls.map(([filter]) => filter?.queryKey)).toEqual(expect.arrayContaining([["tasks", 5, "comments"], ["documents"]]));
});

it("новое содержимое после ошибки получает новый UUID; можно отправить только существующий Markdown", async () => {
    const requests: FormData[] = [];
    vi.spyOn(api, "postForm").mockImplementation(async (_path, body) => { requests.push(body); throw new Error("Временно недоступно"); });
    const { user } = setup();
    await user.click(screen.getByText("Из документов задачи"));
    await user.click(await screen.findByRole("checkbox", { name: "Прикрепить План проекта" }));
    await user.click(screen.getByRole("button", { name: "Отправить" }));
    await screen.findByText(/Временно недоступно/);
    expect(requests[0].get("body_md")).toBe("");
    fireEvent.change(screen.getByLabelText("Текст комментария"), { target: { value: "Добавил пояснение" } });
    await user.click(screen.getByRole("button", { name: "Отправить" }));
    await waitFor(() => expect(requests).toHaveLength(2));
    expect(requests[1].get("request_id")).not.toBe(requests[0].get("request_id"));
});

it("под комментарием читает текущий документ, удаление комментария сохраняет документ", async () => {
    const remove = vi.spyOn(api, "delete").mockResolvedValue(undefined);
    const comment: TaskComment = { id: 12, task_id: 5, author_name: "Иван", body_md: "", created_at: document.created_at, document_refs: [document.ref] };
    const { user, onChange } = setup([comment]);
    await waitFor(() => expect(documentRefArticle()?.textContent).toContain("Уточнённая подпись"));
    expect(api.get).toHaveBeenCalledWith(`/api/v1/documents/${document.ref}`);
    await user.click(screen.getByRole("button", { name: "Удалить" }));
    await waitFor(() => expect(onChange).toHaveBeenCalledTimes(1));
    expect(remove).toHaveBeenCalledExactlyOnceWith("/api/v1/comments/12");
});
function documentRefArticle() { return globalThis.document.querySelector("article"); }
