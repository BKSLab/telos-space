// @vitest-environment jsdom
import { cleanup, createEvent, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { AgentMessage } from "@/lib/types";
import { queryKeys } from "@/lib/api";
import { useArisWidgetStore } from "@/stores/arisWidget";
import { ArisPanel } from "./ArisPanel";

const chat = vi.hoisted(() => {
    const mutation = () => ({ error: null, reset: vi.fn(), mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false, variables: undefined });
    return {
        conversations: { data: { pages: [{ items: [{ id: 1, title: "Планирование" }] }] }, isPending: false, error: null, hasNextPage: false },
        messages: { data: { pages: [{ items: [] as AgentMessage[] }] }, isPending: false, isError: false, error: null, hasNextPage: false },
        mutating: false,
        create: mutation(), send: mutation(), retry: mutation(), decide: mutation(), upload: mutation(), removeUpload: mutation(),
    };
});
vi.mock("@/lib/useArisConversations", () => ({ useArisConversations: () => chat }));
vi.mock("@/lib/useArisPageContext", () => ({ useArisPageContext: () => ({ route: "/chat", project_key: null, task_id: null }) }));
const clients: QueryClient[] = [];
function setup(touch = true) {
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: touch })));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    client.setQueryData(queryKeys.projects, []);
    clients.push(client);
    return render(<QueryClientProvider client={client}><ArisPanel userId={9} embedded /></QueryClientProvider>);
}
beforeEach(() => {
    useArisWidgetStore.setState({ drafts: {}, conversations: { 9: 1 } });
    chat.messages.data.pages[0].items = [];
    chat.send.mutateAsync.mockReset().mockResolvedValue({});
});
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.unstubAllGlobals(); });

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
