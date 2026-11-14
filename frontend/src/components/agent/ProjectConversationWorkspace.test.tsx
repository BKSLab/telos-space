// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { AgentFile, Project } from "@/lib/types";
import { api } from "@/lib/api";
import { useProjectConversationStore } from "@/stores/projectConversations";
import { ProjectConversationWorkspace } from "./ProjectConversationWorkspace";

const { chat } = vi.hoisted(() => {
    const mutation = () => ({ error: null, reset: vi.fn(), mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false, variables: undefined });
    return { chat: {
        conversations: { data: { pages: [{ items: [] as { id: number; title: string }[] }] }, isPending: false, error: null, hasNextPage: false },
        messages: { data: { pages: [{ items: [] }] }, isPending: false, isError: false, error: null, hasNextPage: false },
        mutating: false, create: mutation(), send: mutation(), retry: mutation(), decide: mutation(), upload: mutation(), removeUpload: mutation(),
    } };
});
vi.mock("@/lib/useProjectConversations", () => ({ useProjectConversations: () => chat }));
vi.mock("@/lib/useAuth", () => ({ useCurrentUser: () => ({ data: { id: 9 } }) }));
vi.mock("@/lib/useProjectKnowledgeStatus", () => ({ useProjectKnowledgeStatus: () => ({ data: undefined, error: null }) }));
const clients: QueryClient[] = [];
const file: AgentFile = { id: "private-file", original_name: "private.txt", content_type: "text/plain", size: 5, created_at: "2026-11-15T10:00:00Z" };
function setup() {
    vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true })));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    clients.push(client);
    return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/projects/DEMO/conversations"]}>
        <ProjectConversationWorkspace project={{ id: 1, key: "DEMO", name: "Проект" } as Project} />
    </MemoryRouter></QueryClientProvider>);
}
beforeEach(() => {
    useProjectConversationStore.setState({ conversations: {}, drafts: {} });
    chat.create.mutateAsync.mockReset(); chat.upload.mutateAsync.mockReset();
    chat.send.mutateAsync.mockReset().mockResolvedValue({});
    chat.removeUpload.mutateAsync.mockReset().mockResolvedValue(undefined);
});
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("первый upload создаёт один приватный диалог и переносит актуальный набранный текст", async () => {
    let created!: (value: { id: number }) => void;
    let uploaded!: (value: AgentFile) => void;
    chat.create.mutateAsync.mockImplementation(() => new Promise(resolve => { created = resolve as typeof created; }));
    chat.upload.mutateAsync.mockImplementation(() => new Promise(resolve => { uploaded = resolve as typeof uploaded; }));
    setup();
    fireEvent.change(screen.getByLabelText("Файлы для Ариса"), { target: { files: [new File(["draft"], "private.txt")] } });
    const field = screen.getByRole("textbox", { name: "Вопрос Арису по проекту" }) as HTMLTextAreaElement;
    expect(field.disabled).toBe(false);
    fireEvent.change(field, { target: { value: "Набран после выбора файла" } });
    await act(async () => { created({ id: 7 }); });
    expect(chat.create.mutateAsync).toHaveBeenCalledOnce();
    await waitFor(() => expect(chat.upload.mutateAsync).toHaveBeenCalledOnce());
    expect(useProjectConversationStore.getState().drafts["9:1:7"].content).toBe("Набран после выбора файла");
    fireEvent.change(field, { target: { value: "Продолжил во время загрузки" } });
    expect((screen.getByRole("button", { name: "Спросить" }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => { uploaded(file); });
    await screen.findByRole("button", { name: "Убрать private.txt" });
    expect(useProjectConversationStore.getState().drafts["9:1:7"]).toMatchObject({ content: "Продолжил во время загрузки", files: [file] });
    expect(chat.send.mutateAsync).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Спросить" }));
    await waitFor(() => expect(chat.send.mutateAsync).toHaveBeenCalledOnce());
    expect(chat.send.mutateAsync.mock.calls[0][0]).toMatchObject({ id: 7, content: "Продолжил во время загрузки", fileIds: ["private-file"] });
});

it("unmount проектного private upload удаляет поздний файл только в исходном conversation", async () => {
    useProjectConversationStore.setState({ conversations: { "9:1": 3 } });
    let uploaded!: (value: AgentFile) => void;
    chat.upload.mutateAsync.mockImplementation(() => new Promise(resolve => { uploaded = resolve as typeof uploaded; }));
    const remove = vi.spyOn(api, "delete").mockResolvedValue(undefined);
    const view = setup();
    fireEvent.change(screen.getByLabelText("Файлы для Ариса"), { target: { files: [new File(["draft"], "private.txt")] } });
    await waitFor(() => expect(chat.upload.mutateAsync).toHaveBeenCalledOnce());
    view.unmount();
    await act(async () => { uploaded(file); });
    expect(remove).toHaveBeenCalledExactlyOnceWith("/api/v1/projects/1/aris/conversations/3/files/private-file");
    expect(useProjectConversationStore.getState().drafts["9:1:3"]?.files ?? []).toEqual([]);
});

it("после ошибки создания первого диалога повтор файла создаёт новую попытку и не теряет текст", async () => {
    chat.create.mutateAsync.mockRejectedValueOnce(new Error("Нет сети")).mockResolvedValueOnce({ id: 7 });
    chat.upload.mutateAsync.mockResolvedValue(file);
    setup();
    fireEvent.change(screen.getByRole("textbox", { name: "Вопрос Арису по проекту" }), { target: { value: "Сохранённый вопрос" } });
    fireEvent.change(screen.getByLabelText("Файлы для Ариса"), { target: { files: [new File(["draft"], "private.txt")] } });
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    await screen.findByRole("button", { name: "Убрать private.txt" });
    expect(chat.create.mutateAsync).toHaveBeenCalledTimes(2);
    expect(chat.upload.mutateAsync).toHaveBeenCalledOnce();
    expect(useProjectConversationStore.getState().drafts["9:1:7"]).toMatchObject({ content: "Сохранённый вопрос", files: [file] });
});
