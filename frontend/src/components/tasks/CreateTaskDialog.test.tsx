// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { documentFixture, documentPageFixture } from "@/components/documents/documentFixtures.test-utils";
import { CreateTaskDialog } from "./CreateTaskDialog";

const clients: QueryClient[] = [];
function setup() {
    vi.spyOn(api, "get").mockImplementation(async path => path.includes("/documents?") ? documentPageFixture([documentFixture()]) : path.endsWith("/members") ? [] : { id: 1, username: "owner" });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } });
    clients.push(client);
    const onClose = vi.fn(); const onCreated = vi.fn();
    render(<QueryClientProvider client={client}><MemoryRouter><CreateTaskDialog projectId={1} stages={[]} isOpen onClose={onClose} onCreated={onCreated} /></MemoryRouter></QueryClientProvider>);
    return { user: userEvent.setup(), onClose, onCreated };
}
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); });

it("добавляет несколько upload-черновиков и после частичного отказа повторяет только документы в той же задаче", async () => {
    const post = vi.spyOn(api, "post").mockImplementation(async path => path.endsWith("/tasks") ? { id: 5, key: "TEST-5" } : documentFixture());
    const uploads: FormData[] = []; let fail = true;
    vi.spyOn(api, "postForm").mockImplementation(async (_path, body) => {
        uploads.push(body); const file = body.get("file") as File;
        if (file.name === "second.txt" && fail) { fail = false; throw new Error("Связь прервалась"); }
        return documentFixture({ ref: `opaque-${file.name}`, title: file.name });
    });
    const { user, onClose, onCreated } = setup();
    await user.type(screen.getByLabelText("Название", { exact: true }), "Проверить документы");
    await user.click(screen.getByRole("button", { name: "Добавить документ" }));
    await user.click(screen.getByRole("tab", { name: "Загрузить" }));
    await user.upload(screen.getByLabelText("Выбрать документы", { selector: "input" }), [new File(["Первый"], "first.txt"), new File(["Второй"], "second.txt")]);
    await user.click(screen.getByRole("button", { name: "Добавить в задачу" }));
    expect(await screen.findByLabelText("Подпись к first.txt")).toBeTruthy();
    expect(screen.getByLabelText("Подпись к second.txt")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Создать задачу" }));
    expect(await screen.findByText(/Задача TEST-5 создана; не все документы добавлены/)).toBeTruthy();
    expect(screen.queryByLabelText("Подпись к first.txt")).toBeNull();
    expect(screen.getByLabelText("Подпись к second.txt")).toBeTruthy();
    expect((screen.getByLabelText("Название", { exact: true }) as HTMLInputElement).matches(":disabled")).toBe(true);
    expect(onClose).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Повторить добавление документов" }));
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(onCreated).toHaveBeenCalledWith(expect.objectContaining({ id: 5 }));
    expect(post.mock.calls.filter(([path]) => path === "/api/v1/projects/1/tasks")).toHaveLength(1);
    expect(uploads.map(body => (body.get("file") as File).name)).toEqual(["first.txt", "second.txt", "second.txt"]);
    expect(uploads[1].get("request_id")).toBe(uploads[2].get("request_id"));
    expect(uploads.every(body => body.get("owner_task_id") === "5" && !body.has("project_id"))).toBe(true);
});

it("создаёт Markdown в задаче и связывает выбранный документ проекта каноническими ссылками", async () => {
    const post = vi.spyOn(api, "post").mockImplementation(async path => path === "/api/v1/projects/1/tasks" ? { id: 5, key: "TEST-5" } : documentFixture());
    const { user, onClose } = setup();
    await user.type(screen.getByLabelText("Название", { exact: true }), "Создать результат");
    await user.click(screen.getByRole("button", { name: "Добавить документ" }));
    await user.type(screen.getByLabelText("Название документа"), "Критерии");
    await user.type(screen.getByLabelText("Текст документа · Markdown"), "# Готово");
    await user.click(screen.getByRole("button", { name: "Добавить в задачу" }));
    await user.click(screen.getByRole("button", { name: "Добавить документ" }));
    await user.click(screen.getByRole("tab", { name: "Из проекта" }));
    const dialog = screen.getByRole("dialog", { name: "Добавить документ" });
    await user.click(await within(dialog).findByRole("checkbox", { name: /План проекта/ }));
    await user.click(screen.getByRole("button", { name: "Добавить в задачу" }));
    await user.click(screen.getByRole("button", { name: "Создать задачу" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(post).toHaveBeenCalledWith("/api/v1/documents/test-opaque-document/tasks/5");
    expect(post).toHaveBeenCalledWith("/api/v1/documents", expect.objectContaining({ title: "Критерии", content_md: "# Готово", owner_task_id: 5 }));
});
