// @vitest-environment jsdom
import { act, cleanup, createEvent, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { AgentFile, AgentMessage } from "@/lib/types";
import { api, queryKeys } from "@/lib/api";
import { useArisWidgetStore } from "@/stores/arisWidget";
import { ArisPanel } from "./ArisPanel";
import * as timezone from "@/lib/timezone";
import { useArisConversations } from "@/lib/useArisConversations";

const chat = vi.hoisted(() => {
    const mutation = () => ({ error: null, reset: vi.fn(), mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false, variables: undefined });
    return {
        conversations: { data: { pages: [{ items: [{ id: 1, title: "Планирование" }] }] }, isPending: false, error: null, hasNextPage: false },
        messages: { data: { pages: [{ items: [] as AgentMessage[] }] }, isPending: false, isError: false, error: null, hasNextPage: false },
        mutating: false,
        create: mutation(), send: mutation(), retry: mutation(), decide: mutation(), upload: mutation(), removeUpload: mutation(),
    };
});
vi.mock("@/lib/useArisConversations", () => ({ useArisConversations: vi.fn(() => chat) }));
vi.mock("@/lib/useArisPageContext", () => ({ useArisPageContext: () => ({ route: "/chat", project_key: null, task_id: null }) }));
const clients: QueryClient[] = [];
function setup(touch = true, route = "/chat") {
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: touch })));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    client.setQueryData(queryKeys.projects, []);
    clients.push(client);
    return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[route]}><ArisPanel userId={9} embedded /></MemoryRouter></QueryClientProvider>);
}
beforeEach(() => {
    useArisWidgetStore.setState({ drafts: {}, conversations: { 9: 1 } });
    chat.messages.data.pages[0].items = [];
    vi.mocked(useArisConversations).mockClear();
    chat.send.mutateAsync.mockReset().mockResolvedValue({});
    chat.conversations.data.pages[0].items = [{ id: 1, title: "Планирование" }, { id: 2, title: "Другой диалог" }];
    chat.upload.mutateAsync.mockReset();
    chat.removeUpload.mutateAsync.mockReset().mockResolvedValue(undefined);
});
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("не открывает экранную клавиатуру при входе и отправляет личный вопрос отдельной кнопкой", async () => {
    setup();
    const field = screen.getByRole("textbox", { name: /^Вопрос Арису$/ });
    expect(document.activeElement).not.toBe(field);
    fireEvent.change(field, { target: { value: "Первая строка" } });
    const enter = createEvent.keyDown(field, { key: "Enter", bubbles: true, cancelable: true });
    fireEvent(field, enter);
    expect(enter.defaultPrevented).toBe(false);
    expect(chat.send.mutateAsync).not.toHaveBeenCalled();
    fireEvent.change(field, { target: { value: "Первая строка\nВторая строка" } });
    fireEvent.click(screen.getByRole("button", { name: "Отправить Арису (Enter)" }));
    await waitFor(() => expect(chat.send.mutateAsync).toHaveBeenCalledOnce());
    expect(chat.send.mutateAsync.mock.calls[0][0]).toMatchObject({ id: 1, content: "Первая строка\nВторая строка", pageContext: { route: "/chat", project_key: null, task_id: null } });
});

it("сохраняет быстрый ввод с обычной клавиатуры", async () => {
    setup(false);
    const field = screen.getByRole("textbox", { name: /^Вопрос Арису$/ });
    expect(document.activeElement).toBe(field);
    fireEvent.change(field, { target: { value: "Что сделать первым?" } });
    fireEvent.keyDown(field, { key: "Enter" });
    await waitFor(() => expect(chat.send.mutateAsync).toHaveBeenCalledOnce());
});

it("разрешает подготовить черновик во время ответа и блокирует повторную отправку", () => {
    chat.messages.data.pages[0].items = [{ id: 4, role: "assistant", content: "", status: "processing", actions: [], files: [], sources: [] } as unknown as AgentMessage];
    setup();
    const field = screen.getByRole("textbox", { name: /^Вопрос Арису$/ }) as HTMLTextAreaElement;
    expect(field.disabled).toBe(false);
    fireEvent.change(field, { target: { value: "Следующий вопрос" } });
    expect(useArisWidgetStore.getState().drafts["9:1"].content).toBe("Следующий вопрос");
    expect((screen.getByRole("button", { name: "Отправить Арису (Enter)" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.keyDown(field, { key: "Enter", ctrlKey: true });
    expect(chat.send.mutateAsync).not.toHaveBeenCalled();
});

const file: AgentFile = { id: "late-upload", original_name: "draft.txt", content_type: "text/plain", size: 5, created_at: "2026-11-15T10:00:00Z" };

it("pending upload разрешает ввод, блокирует Send и удаляется после смены личного диалога", async () => {
    let finish!: (value: AgentFile) => void;
    chat.upload.mutateAsync.mockImplementation(() => new Promise(resolve => { finish = resolve as typeof finish; }));
    const remove = vi.spyOn(api, "delete").mockResolvedValue(undefined);
    setup();
    fireEvent.change(screen.getByLabelText("Файлы для Ариса"), { target: { files: [new File(["draft"], "draft.txt")] } });
    await waitFor(() => expect(chat.upload.mutateAsync).toHaveBeenCalledOnce());
    const field = screen.getByRole("textbox", { name: /^Вопрос Арису$/ }) as HTMLTextAreaElement;
    expect(field.disabled).toBe(false);
    fireEvent.change(field, { target: { value: "Текст во время upload" } });
    expect((screen.getByRole("button", { name: "Отправить Арису (Enter)" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.keyDown(field, { key: "Enter", ctrlKey: true });
    expect(chat.send.mutateAsync).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("combobox", { name: "Диалог с Арисом" }), { target: { value: "2" } });
    expect(screen.queryByRole("button", { name: "Отменить загрузку draft.txt" })).toBeNull();
    fireEvent.change(field, { target: { value: "Другой приватный draft" } });
    await act(async () => { finish(file); });
    expect(remove).toHaveBeenCalledExactlyOnceWith("/api/v1/aris/conversations/1/files/late-upload");
    expect(useArisWidgetStore.getState().drafts["9:2"]).toMatchObject({ content: "Другой приватный draft", files: [] });
    expect(useArisWidgetStore.getState().drafts["9:1"]).toMatchObject({ content: "Текст во время upload", files: [] });
});

it("готовый private draft публикует только file_ids после явного Send и не удаляется при закрытии", async () => {
    chat.upload.mutateAsync.mockResolvedValue(file);
    const remove = vi.spyOn(api, "delete").mockResolvedValue(undefined);
    const view = setup();
    fireEvent.change(screen.getByLabelText("Файлы для Ариса"), { target: { files: [new File(["draft"], "draft.txt")] } });
    await screen.findByRole("button", { name: "Убрать draft.txt" });
    expect(chat.send.mutateAsync).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("textbox", { name: /^Вопрос Арису$/ }), { target: { value: "Опубликовать файл" } });
    fireEvent.click(screen.getByRole("button", { name: "Отправить Арису (Enter)" }));
    await waitFor(() => expect(chat.send.mutateAsync).toHaveBeenCalledOnce());
    expect(chat.send.mutateAsync.mock.calls[0][0]).toMatchObject({ id: 1, content: "Опубликовать файл", fileIds: ["late-upload"] });
    view.unmount();
    expect(remove).not.toHaveBeenCalled();
});

it("повтор после ошибки сохраняет IANA timezone и остальной page_context исходного запроса", async () => {
    const zone = vi.spyOn(timezone, "browserTimezone").mockReturnValue("Europe/Samara");
    chat.send.mutateAsync.mockRejectedValueOnce(new Error("Нет сети"));
    setup();
    fireEvent.change(screen.getByRole("textbox", { name: /^Вопрос Арису$/ }), { target: { value: "Что обсуждали сегодня?" } });
    fireEvent.click(screen.getByRole("button", { name: "Отправить Арису (Enter)" }));
    await waitFor(() => expect(chat.send.mutateAsync).toHaveBeenCalledOnce());
    const first = chat.send.mutateAsync.mock.calls[0][0];
    expect(first.pageContext).toEqual({ route: "/chat", project_key: null, task_id: null, timezone: "Europe/Samara" });
    zone.mockReturnValue("Asia/Tokyo");
    fireEvent.click(screen.getByRole("button", { name: "Отправить Арису (Enter)" }));
    await waitFor(() => expect(chat.send.mutateAsync).toHaveBeenCalledTimes(2));
    expect(chat.send.mutateAsync.mock.calls[1][0]).toEqual(first);
});

it("личная deep link выбирает исходный диалог вместо remembered и подсвечивает сообщение", async () => {
    chat.messages.data.pages[0].items = [{ id: 42, conversation_id: 2, request_id: "source", role: "user", content: "Договорились отправить в пятницу", sources: [], status: "completed", error: null, created_at: "2026-11-14T10:00:00Z" }];
    setup(true, "/chat?conversation=2&message=42");
    expect(useArisConversations).toHaveBeenCalledWith(2, true, 42);
    const target = screen.getByLabelText("Выбранное сообщение");
    expect(target.dataset.chatMessage).toBe("42");
    expect(target.dataset.highlighted).toBe("true");
    expect(document.activeElement).toBe(target);
    expect(useArisWidgetStore.getState().conversations[9]).toBe(2);
    fireEvent.click(screen.getByRole("button", { name: "К последним сообщениям" }));
    await waitFor(() => expect(useArisConversations).toHaveBeenLastCalledWith(2, true, null));
    expect(screen.queryByLabelText("Выбранное сообщение")).toBeNull();
});
