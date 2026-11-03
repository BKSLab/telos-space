// @vitest-environment jsdom
import { cleanup, createEvent, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { emptyChatDraft, useChatUiStore } from "@/stores/chat";
import { ChatComposer } from "./ChatComposer";

const chat = vi.hoisted(() => ({
    projectId: 1, userId: 9, canWrite: true, state: "connected", online: [9], typing: [],
    info: { members: [{ id: 9, username: "anna", display_name: "Анна" }] },
    send: vi.fn(), command: vi.fn(), signalTyping: vi.fn(),
}));
vi.mock("@/lib/useProjectChat", () => ({ useProjectChat: () => chat }));
const clients: QueryClient[] = [];

function touchDevice(touch = true) {
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: touch })));
}
function setup() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    clients.push(client);
    return render(<QueryClientProvider client={client}><ChatComposer editing={null} onCancelEdit={vi.fn()} /></QueryClientProvider>);
}
beforeEach(() => {
    useChatUiStore.setState({ drafts: {}, editing: {}, busy: {} });
    chat.send.mockReset().mockResolvedValue({});
    chat.command.mockReset().mockResolvedValue({});
    touchDevice(false);
});
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.unstubAllGlobals(); });

it("оставляет мобильный Enter для новой строки, а кнопку — для отправки полного сообщения", async () => {
    touchDevice();
    setup();
    const field = screen.getByRole("combobox", { name: "Сообщение проекту" });
    fireEvent.change(field, { target: { value: "Первая строка" } });
    const enter = createEvent.keyDown(field, { key: "Enter", bubbles: true, cancelable: true });
    fireEvent(field, enter);
    expect(enter.defaultPrevented).toBe(false);
    expect(chat.send).not.toHaveBeenCalled();
    fireEvent.change(field, { target: { value: "Первая строка\nВторая строка" } });
    fireEvent.click(screen.getByRole("button", { name: /^Отправить$/ }));
    await waitFor(() => expect(chat.send).toHaveBeenCalledOnce());
    expect(chat.send.mock.calls[0][0].content).toBe("Первая строка\nВторая строка");
});

it.each([false, true])("сохраняет отправку с клавиатуры: touch=%s", async touch => {
    touchDevice(touch);
    setup();
    const field = screen.getByRole("combobox", { name: "Сообщение проекту" });
    fireEvent.change(field, { target: { value: "Проверить сроки" } });
    fireEvent.keyDown(field, { key: "Enter", ctrlKey: touch });
    await waitFor(() => expect(chat.send).toHaveBeenCalledOnce());
});

it("не отправляет при Shift+Enter и незавершённом вводе IME", () => {
    setup();
    const field = screen.getByRole("combobox", { name: "Сообщение проекту" });
    fireEvent.change(field, { target: { value: "Сообщение" } });
    fireEvent.keyDown(field, { key: "Enter", shiftKey: true });
    fireEvent.keyDown(field, { key: "Enter", isComposing: true });
    fireEvent.keyDown(field, { key: "Enter", keyCode: 229 });
    expect(chat.send).not.toHaveBeenCalled();
});

it("не выбирает упоминание мобильным Enter и позволяет закрыть список касанием", () => {
    touchDevice();
    setup();
    const field = screen.getByRole("combobox", { name: "Сообщение проекту" });
    fireEvent.change(field, { target: { value: "@", selectionStart: 1 } });
    expect(screen.getByRole("option", { name: /Анна/ })).toBeTruthy();
    fireEvent.keyDown(field, { key: "Enter" });
    expect(useChatUiStore.getState().drafts["9:1"].mentions).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Закрыть выбор" }));
    expect(screen.queryByRole("listbox")).toBeNull();
    expect(chat.send).not.toHaveBeenCalled();
});

it("сохраняет черновик и квитанцию после ошибки, повторяет тот же запрос", async () => {
    chat.send.mockRejectedValueOnce(new Error("Нет соединения"));
    useChatUiStore.getState().updateDraft("9:1", { ...emptyChatDraft(), content: "Не потерять сообщение", clientMessageId: "same-request" });
    setup();
    fireEvent.click(screen.getByRole("button", { name: /^Отправить$/ }));
    await screen.findByRole("alert");
    expect(useChatUiStore.getState().drafts["9:1"].content).toBe("Не потерять сообщение");
    fireEvent.click(screen.getByRole("button", { name: /^Отправить$/ }));
    await waitFor(() => expect(chat.send).toHaveBeenCalledTimes(2));
    expect(chat.send.mock.calls.map(call => call[0].client_message_id)).toEqual(["same-request", "same-request"]);
    await waitFor(() => expect(useChatUiStore.getState().drafts["9:1"]).toBeUndefined());
});
