import { afterEach, describe, expect, it, vi } from "vitest";
import { applyChatMessage, chatAuthorName, isTaskMove, mergeChatMessages, taskMoveText, type ChatEvent, type ChatMessage, type ChatTaskMove } from "@/lib/projectChat";
import { ProjectChatTransport } from "@/lib/projectChatTransport";

const row = (patch: Partial<ChatMessage> = {}): ChatMessage => ({
    id: 1, chat_id: 1, project_id: 1, seq: 1, author_type: "USER", author: { id: 1, username: "anna", display_name: "Анна" }, client_message_id: "a", content: "Сообщение", revision: 1, created_at: "2026-09-21T10:00:00Z", edited_at: null, deleted_at: null, reply: null, mentions: [], entities: [], attachments: [], reactions: [], ...patch,
});

describe("переносы и авторы чата", () => {
    const move: ChatTaskMove = { task_id: 8, from_key: "DEMO-8", user_id: 2, direction: "out", target_kind: "PROJECT", target_name: null, to_key: null };

    it("называет входящий, исходящий и личный перенос без раскрытия чужого проекта", () => {
        const members = [{ id: 2, username: "oleg", display_name: "Олег" }];
        expect(taskMoveText(move, members)).toBe("Задача DEMO-8 перенесена в другой проект — Олег");
        expect(taskMoveText({ ...move, target_kind: "PERSONAL", to_key: "PERSONAL-3" }, members)).toBe("Задача DEMO-8 перенесена в разовые задачи (PERSONAL-3) — Олег");
        expect(taskMoveText({ ...move, direction: "in", to_key: "DEMO-9" }, [])).toBe("Задача DEMO-8 перенесена в этот проект: теперь DEMO-9");
        expect(taskMoveText(move, [{ ...members[0], display_name: " " }])).toContain("— oleg");
    });

    it("пропускает неполные и неподдерживаемые данные переноса", () => {
        expect(isTaskMove(move)).toBe(true);
        expect(isTaskMove({ task_id: 8, from_key: "DEMO-8", direction: "out" })).toBe(false);
        expect(isTaskMove({ ...move, task_id: NaN })).toBe(false);
        expect(isTaskMove({ ...move, target_kind: "FLOW" } as unknown as ChatEvent["data"])).toBe(false);
    });

    it("подписывает Ариса, локального пользователя и старый ответ с отсутствующим автором", () => {
        expect(chatAuthorName({ author_type: "AGENT", author: null })).toBe("Арис");
        expect(chatAuthorName({ author: row().author })).toBe("Анна");
        expect(chatAuthorName({ author: { id: 1, username: "anna", display_name: " " } })).toBe("anna");
        expect(chatAuthorName({ author: null }, "Удалённый пользователь")).toBe("Удалённый пользователь");
    });

    it("сохраняет тип автора Ариса в preview после обновления исходного сообщения", () => {
        const original = row({ author_type: "AGENT", author: null });
        const reply = row({ id: 2, seq: 2, client_message_id: "reply", reply: { id: 1, seq: 1, author: null, content: "", deleted: false } });
        const history = applyChatMessage({ messages: [reply], has_more: false, event_cursor: 0 }, original);
        expect(chatAuthorName(history.messages.find((message) => message.id === 2)!.reply!)).toBe("Арис");
    });
});

describe("единый кэш чата", () => {
    it("схлопывает оптимистичную отправку, ack и broadcast", () => {
        const optimistic = row({ id: -1, delivery: "sending" });
        const messages = mergeChatMessages(mergeChatMessages([optimistic], [row()]), [row({ eventSeq: 3 })]);
        expect(messages).toHaveLength(1);
        expect(messages[0].id).toBe(1);
        expect(messages[0].delivery).toBeUndefined();
        expect(mergeChatMessages(messages, [{ ...optimistic, delivery: "failed" }])[0].delivery).toBeUndefined();
    });

    it("не смешивает одинаковые UUID двух авторов и не откатывает новую версию", () => {
        const own = row({ revision: 2, content: "Правка" });
        const other = row({ id: 2, seq: 2, author: { id: 2, username: "oleg", display_name: "Олег" } });
        const merged = mergeChatMessages([own, other], [row()]);
        expect(merged).toHaveLength(2);
        expect(merged[0].content).toBe("Правка");
    });

    it("удаление обновляет также preview ответа", () => {
        const reply = row({ id: 2, seq: 2, client_message_id: "b", reply: { id: 1, seq: 1, author: row().author, content: "Старый текст", deleted: false } });
        const history = applyChatMessage({ messages: [row(), reply], has_more: false, event_cursor: 2 }, row({ revision: 2, content: "", deleted_at: "2026-09-21T10:01:00Z" }));
        expect(history.messages[1].reply).toMatchObject({ deleted: true, content: "Сообщение удалено" });
    });

    it("поздний ack не откатывает уже доставленную реакцию", () => {
        const current = row({ eventSeq: 5, reactions: [{ reaction: "👍", user_ids: [2] }] });
        expect(mergeChatMessages([current], [row()])[0].reactions).toEqual(current.reactions);
    });
});

class FakeSocket {
    readyState = 1;
    onmessage: ((event: { data: string }) => void) | null = null;
    onclose: ((event: { code: number }) => void) | null = null;
    onerror: (() => void) | null = null;
    sent: Record<string, unknown>[] = [];
    respondToPings = true;
    send(data: string) {
        const event = JSON.parse(data);
        this.sent.push(event);
        if (event.type === "ping" && this.respondToPings) this.receive({ type: "ack", request_id: event.request_id, data: { pong: true } });
    }
    close(code = 1000) { this.readyState = 3; this.onclose?.({ code }); }
    receive(data: object) { this.onmessage?.({ data: JSON.stringify(data) }); }
}
const microtasks = async () => { for (let index = 0; index < 10; index += 1) await Promise.resolve(); };
const event = (seq: number): ChatEvent => ({ type: "message.updated", project_id: 1, seq, data: { message_id: seq } });
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe("WebSocket reconnect/resync", () => {
    it("закрывает зависшее соединение, если сервер перестал отвечать на heartbeat", async () => {
        vi.useFakeTimers();
        const socket = new FakeSocket(); socket.respondToPings = false;
        const state = vi.fn();
        const transport = new ProjectChatTransport({ url: "ws://localhost", cursor: 0, createSocket: () => socket as unknown as WebSocket, fetchEvents: async () => ({ events: [], cursor: 0, has_more: false }), onEvent: vi.fn(), onState: state, onReady: vi.fn() });
        transport.start(); socket.receive({ type: "ready", data: { can_write: true, heartbeat_seconds: 2 } }); await microtasks();
        await vi.advanceTimersByTimeAsync(6000);
        expect(socket.readyState).toBe(3);
        expect(state).toHaveBeenLastCalledWith("reconnecting");
        transport.stop();
    });
    it("восстанавливает пропуск событий и не опрашивает сообщения таймером", async () => {
        vi.useFakeTimers();
        const socket = new FakeSocket();
        const seen = vi.fn();
        const fetchEvents = vi.fn().mockResolvedValueOnce({ events: [], cursor: 0, has_more: false }).mockResolvedValueOnce({ events: [event(1), event(2)], cursor: 2, has_more: false });
        const transport = new ProjectChatTransport({ url: "ws://localhost", cursor: 0, createSocket: () => socket as unknown as WebSocket, fetchEvents, onEvent: seen, onState: vi.fn(), onReady: vi.fn() });
        transport.start(); socket.receive({ type: "ready", data: { can_write: true, heartbeat_seconds: 2 } }); await microtasks();
        socket.receive(event(2)); await microtasks();
        socket.receive(event(2)); socket.receive(event(3));
        expect(seen.mock.calls.map(([value]) => value.seq)).toEqual([1, 2, 3]);
        await vi.advanceTimersByTimeAsync(10000);
        expect(fetchEvents).toHaveBeenCalledTimes(2);
        expect(socket.sent.filter((value) => value.type === "ping")).toHaveLength(5);
        transport.stop();
    });

    it("ack сопоставляется запросу, отказ завершает ожидание без автоматического повтора", async () => {
        const socket = new FakeSocket();
        const transport = new ProjectChatTransport({ url: "ws://localhost", cursor: 0, createSocket: () => socket as unknown as WebSocket, fetchEvents: async () => ({ events: [], cursor: 0, has_more: false }), onEvent: vi.fn(), onState: vi.fn(), onReady: vi.fn() });
        transport.start(); socket.receive({ type: "ready", data: { can_write: true } }); await microtasks();
        const accepted = transport.command("message.send", { client_message_id: "stable-id" });
        socket.receive({ type: "ack", request_id: socket.sent.at(-1)!.request_id, data: { message: row() } });
        expect((await accepted).message?.id).toBe(1);
        const failed = transport.command("message.send", { client_message_id: "stable-id" });
        const rejection = expect(failed).rejects.toThrow("Соединение прервано");
        socket.close(4403);
        await rejection;
        expect(socket.sent.filter((value) => value.type === "message.send")).toHaveLength(2);
        transport.stop();
    });

    it("reconnect запрашивает дельту с последнего применённого события", async () => {
        vi.useFakeTimers(); vi.spyOn(Math, "random").mockReturnValue(0);
        const sockets: FakeSocket[] = [];
        const fetchEvents = vi.fn().mockResolvedValue({ events: [], cursor: 0, has_more: false });
        const transport = new ProjectChatTransport({ url: "ws://localhost", cursor: 4, createSocket: () => { const socket = new FakeSocket(); sockets.push(socket); return socket as unknown as WebSocket; }, fetchEvents, onEvent: vi.fn(), onState: vi.fn(), onReady: vi.fn() });
        transport.start(); sockets[0].receive({ type: "ready", data: { can_write: true } }); await microtasks();
        sockets[0].receive(event(5)); sockets[0].close(1013);
        await vi.advanceTimersByTimeAsync(501);
        expect(sockets).toHaveLength(2);
        sockets[1].receive({ type: "ready", data: { can_write: true } }); await microtasks();
        expect(fetchEvents).toHaveBeenLastCalledWith(5);
        transport.stop();
    });
});
