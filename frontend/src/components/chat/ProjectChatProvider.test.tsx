// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, queryKeys } from "@/lib/api";
import { chatKeys, type ChatEntity, type ChatEvent, type ChatHistory, type ChatMessage, type ChatTaskMove, type ProjectChat } from "@/lib/projectChat";
import { useProjectChat } from "@/lib/useProjectChat";
import { ProjectChatProvider } from "./ProjectChatProvider";

interface Connection { onEvent: (event: ChatEvent, replay: boolean) => void }
const { connections, auth } = vi.hoisted(() => ({ connections: [] as Connection[], auth: { id: 1 } }));
vi.mock("@/lib/useAuth", () => ({ useCurrentUser: () => ({ data: auth }) }));
vi.mock("@/lib/projectChatTransport", () => ({
    ProjectChatTransport: class {
        constructor(options: Connection) { connections.push(options); }
        start() {}
        stop() {}
    },
}));

const members = [{ id: 1, username: "anna", display_name: "Анна" }, { id: 2, username: "oleg", display_name: "Олег" }];
const move: ChatTaskMove = { task_id: 42, from_key: "DEMO-42", user_id: 2, direction: "out", target_kind: "PROJECT", target_name: null, to_key: null };
const message = (patch: Partial<ChatMessage> = {}): ChatMessage => ({ id: 7, seq: 1, chat_id: 1, project_id: 1, author_type: "USER", author: members[1], client_message_id: "message", content: "Ответ", revision: 1, created_at: "2026-10-22T10:00:00Z", edited_at: null, deleted_at: null, reply: null, mentions: [], entities: [], attachments: [], reactions: [], ...patch });

function Notices() {
    const chat = useProjectChat();
    return <ul aria-label="События переноса">{chat.taskMoves.map((notice) => <li key={notice.seq}>{notice.text}<button onClick={() => chat.dismissTaskMove(notice.seq)}>Скрыть {notice.seq}</button></li>)}</ul>;
}

function setup() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    for (const [projectId, userId] of [[1, 1], [2, 1], [2, 2]]) client.setQueryData<ProjectChat>(chatKeys.info(projectId, userId), { id: projectId, project_id: projectId, event_cursor: 0, last_read_seq: 0, unread_count: 0, members });
    const node = (projectId = 1) => <QueryClientProvider client={client}><MemoryRouter><ProjectChatProvider projectId={projectId} projectKey={`DEMO${projectId}`}><Notices /></ProjectChatProvider></MemoryRouter></QueryClientProvider>;
    const view = render(node());
    return { client, switchProject: (projectId: number) => view.rerender(node(projectId)) };
}

function emit(seq: number, data: ChatEvent["data"] = move, projectId = 1, replay = false) {
    act(() => connections.at(-1)!.onEvent({ type: "task.moved", project_id: projectId, seq, data }, replay));
}

afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    connections.length = 0;
    auth.id = 1;
});

describe("события переноса в общем чате", () => {
    it("обновляет открытые детали по событиям задач и документов только текущего проекта", () => {
        const { client } = setup();
        const taskKey = [...queryKeys.task(42), "chat-preview", 1];
        client.setQueryData(taskKey, { title: "Прежнее описание" });
        client.setQueryData(queryKeys.document("opaque-document"), { title: "Прежний документ" });
        client.setQueryData(queryKeys.documentText("opaque-document"), { text: "Прежний текст" });
        const event: ChatEvent = { type: "entity.updated", project_id: 99, data: { entity_type: "TASK", entity_id: 42 } };
        act(() => connections.at(-1)!.onEvent(event, false));
        expect(client.getQueryState(taskKey)?.isInvalidated).toBe(false);
        act(() => connections.at(-1)!.onEvent({ ...event, project_id: 1 }, false));
        expect(client.getQueryState(taskKey)?.isInvalidated).toBe(true);
        expect(client.getQueryState(queryKeys.document("opaque-document"))?.isInvalidated).toBe(false);
        act(() => connections.at(-1)!.onEvent({ ...event, project_id: 1, data: { entity_type: "DOCUMENT", document_ref: "opaque-document" } }, false));
        expect(client.getQueryState(queryKeys.document("opaque-document"))?.isInvalidated).toBe(true);
        expect(client.getQueryState(queryKeys.documentText("opaque-document"))?.isInvalidated).toBe(true);
        client.clear();
    });

    it("сохраняет разные документные refs при hydration и удалении одной карточки", async () => {
        const resolve = vi.spyOn(api, "post").mockResolvedValue([]);
        const { client } = setup();
        const document = (ref: string): ChatEntity => ({ entity_type: "DOCUMENT", entity_id: null, document_ref: ref, title: ref, available: true, href: `/projects/DEMO1/docs/${ref}`, subtitle: null, status: null });
        client.setQueryData<ChatHistory>(chatKeys.messages(1, 1), { messages: [message({ entities: [document("opaque-file"), document("opaque-attachment")] })], initialized: true, event_cursor: 0, has_more: false });
        act(() => connections.at(-1)!.onEvent({ type: "entity.updated", project_id: 1, data: { entity_type: "DOCUMENT", document_ref: "opaque-file" } }, false));
        await waitFor(() => expect(resolve).toHaveBeenCalledWith("/api/v1/projects/1/chat/entities/resolve", [{ entity_type: "DOCUMENT", entity_id: null, document_ref: "opaque-file" }]));
        await waitFor(() => expect(client.getQueryData<ChatHistory>(chatKeys.messages(1, 1))?.messages[0].entities[0].available).toBe(false));
        expect(client.getQueryData<ChatHistory>(chatKeys.messages(1, 1))?.messages[0].entities[1].available).toBe(true);
        client.clear();
    });

    it("обновляет проект, WBS, зависимости и личный список; закрывает старую карточку задачи", async () => {
        const resolve = vi.spyOn(api, "post").mockResolvedValue([]);
        const { client } = setup();
        const invalidated = [queryKeys.wbs(1), queryKeys.taskDependencies(1), queryKeys.tasks(1), queryKeys.tasks(2), queryKeys.myTasks, queryKeys.dashboard, queryKeys.task(42), queryKeys.taskActivity(42)];
        invalidated.forEach((key) => client.setQueryData(key, []));
        const ref: ChatEntity = { entity_type: "TASK", entity_id: 42, title: "DEMO-42", available: true, href: "/projects/DEMO1?task=42", subtitle: null, status: "NEW" };
        client.setQueryData<ChatHistory>(chatKeys.messages(1, 1), { messages: [message({ entities: [ref] })], initialized: true, event_cursor: 0, has_more: false });
        emit(1);
        expect(screen.getByText("Задача DEMO-42 перенесена в другой проект — Олег")).toBeTruthy();
        invalidated.forEach((key) => expect(client.getQueryState(key)?.isInvalidated).toBe(true));
        await waitFor(() => expect(resolve).toHaveBeenCalledWith("/api/v1/projects/1/chat/entities/resolve", [{ entity_type: "TASK", entity_id: 42 }]));
        await waitFor(() => expect(client.getQueryData<ChatHistory>(chatKeys.messages(1, 1))?.messages[0].entities[0]).toMatchObject({ available: false, href: null }));
        client.clear();
    });

    it("не смешивает проекты и пользователей, ограничивает список и схлопывает replay", () => {
        const { client, switchProject } = setup();
        emit(1, move, 99);
        emit(1, { task_id: 42 });
        expect(screen.getByRole("list").children).toHaveLength(0);
        emit(1);
        emit(1, move, 1, true);
        expect(screen.getByRole("list").children).toHaveLength(1);
        for (let seq = 2; seq <= 6; seq += 1) emit(seq, { ...move, from_key: `DEMO-${seq}` });
        expect(screen.getByRole("list").children).toHaveLength(5);
        expect(screen.queryByText(/DEMO-42/)).toBeNull();
        fireEvent.click(screen.getByRole("button", { name: "Скрыть 6" }));
        expect(screen.getByRole("list").children).toHaveLength(4);
        switchProject(2);
        expect(screen.getByRole("list").children).toHaveLength(0);
        emit(1, { ...move, direction: "in", to_key: "DEMO2-3" }, 2);
        expect(screen.getByText(/теперь DEMO2-3/)).toBeTruthy();
        auth.id = 2;
        switchProject(2);
        expect(screen.getByRole("list").children).toHaveLength(0);
        client.clear();
    });

    it("подписывает уведомление Арисом и не показывает пустое сообщение во время подготовки ответа", () => {
        const { client } = setup();
        const thinking = message({ author_type: "AGENT", author: null, content: "", agent: { name: "Арис", status: "processing", error: null, requester: members[0], request_message_id: 1, sources: [], actions: [] } });
        act(() => connections.at(-1)!.onEvent({ type: "message.created", project_id: 1, seq: 1, data: { message: thinking } }, false));
        expect(screen.queryByRole("complementary")).toBeNull();
        act(() => connections.at(-1)!.onEvent({ type: "message.created", project_id: 1, seq: 2, data: { message: message({ id: 8, author_type: "AGENT", author: null, content: "План готов" }) } }, false));
        expect(screen.getByText("Арис: План готов")).toBeTruthy();
        client.clear();
    });
});
