/** Общий контракт HTTP/WS. Серверный seq задаёт порядок внутри проекта. */
import type { AgentToolRun, KnowledgeSource } from "@/lib/types";
import { ARIS_NAME } from "@/lib/aris";

export type ChatEntityType = "TASK" | "DOCUMENT" | "RISK" | "MILESTONE" | "WBS_NODE";
/** Человек в чате — та же ссылка на пользователя, что и во всём API. */
export interface ChatUser { id: number; username: string; display_name: string }
export interface ChatEntityRef { entity_type: ChatEntityType; entity_id: number }
export interface ChatEntity extends ChatEntityRef {
    title: string; available: boolean; status: string | null; subtitle: string | null; href: string | null;
}
export interface ChatAttachment {
    id: string; original_name: string; content_type: string; size_bytes: number;
    message_id: number | null; created_at: string;
}
/** USER — участник, AGENT — Арис (у него author = null), docs/agent.md. */
export type ChatAuthorType = "USER" | "AGENT";
export interface ChatReply { id: number; seq: number; author_type?: ChatAuthorType; author: ChatUser | null; content: string; deleted: boolean }
/** Состояние ответа Ариса: queued/processing — «Арис думает», completed — ответ, failed — ошибка (текст уже в content). */
export interface ChatAgentState {
    name: string;
    status: "queued" | "processing" | "completed" | "failed";
    error: string | null;
    /** Что Арис делает сейчас, пока готовит ответ («Ищу задачи…»); null — статуса нет или ответ готов. */
    progress?: string | null;
    requester: ChatUser | null;
    request_message_id: number;
    sources: KnowledgeSource[];
    actions: AgentToolRun[];
}
export interface ChatMessageBody {
    content: string; mentions: number[]; entities: ChatEntityRef[]; attachment_ids: string[];
}
export interface ChatMessageCreate extends ChatMessageBody { client_message_id: string; reply_to_message_id: number | null }
export interface ChatMessage {
    id: number; chat_id: number; project_id: number; seq: number; author_type: ChatAuthorType;
    author: ChatUser | null; client_message_id: string; content: string; revision: number;
    created_at: string; edited_at: string | null; deleted_at: string | null;
    reply: ChatReply | null; mentions: ChatUser[]; entities: ChatEntity[]; attachments: ChatAttachment[];
    reactions: { reaction: string; user_ids: number[] }[];
    /** Только у сообщений Ариса; у людей null. */
    agent?: ChatAgentState | null;
    /** Только клиентские поля оптимистичной строки, не отдельное хранилище истории. */
    delivery?: "sending" | "failed";
    error?: string;
    pendingBody?: ChatMessageCreate;
    eventSeq?: number;
}
export interface ProjectChat {
    id: number; project_id: number; event_cursor: number; last_read_seq: number; unread_count: number; members: ChatUser[];
}
export interface ChatMessagePage { messages: ChatMessage[]; has_more: boolean; event_cursor: number }
export interface ChatHistory extends ChatMessagePage { initialized?: boolean }
export interface ChatEvent {
    type: string; project_id: number; seq?: number;
    data: { message?: ChatMessage; message_id?: number; user_id?: number; user_ids?: number[]; last_read_seq?: number; entity_type?: ChatEntityType; entity_id?: number } & Partial<ChatTaskMove>;
}
/** Событие `task.moved`: задача ушла из проекта чата (`out`) или пришла в него (`in`). */
export interface ChatTaskMove {
    task_id: number; from_key: string; user_id: number; direction: "in" | "out";
    target_kind: "PROJECT" | "PERSONAL";
    /** У ухода в чужой проект — null: старой команде его не называем. */
    target_name: string | null; to_key: string | null;
}
export interface ChatTaskMoveNotice { seq: number; text: string; taskId: number; direction: ChatTaskMove["direction"] }

/** Строка чата о переносе задачи; `members` — чтобы подписать, кто перенёс. */
export function taskMoveText(move: ChatTaskMove, members: ChatUser[]): string {
    const member = members.find((item) => item.id === move.user_id);
    const who = member ? chatAuthorName({ author: member }, "") : "";
    const by = who ? ` — ${who}` : "";
    if (move.direction === "in") return `Задача ${move.from_key} перенесена в этот проект: теперь ${move.to_key ?? "новый номер"}${by}`;
    const where = move.target_kind === "PERSONAL" ? "в разовые задачи" : "в другой проект";
    return `Задача ${move.from_key} перенесена ${where}${move.to_key ? ` (${move.to_key})` : ""}${by}`;
}

/** Данные события похожи на перенос задачи — иначе событие пропускаем. */
export function isTaskMove(data: ChatEvent["data"]): data is ChatEvent["data"] & ChatTaskMove {
    return Number.isSafeInteger(data.task_id) && data.task_id! > 0 && typeof data.from_key === "string" && data.from_key.length > 0
        && Number.isSafeInteger(data.user_id) && data.user_id! > 0 && (data.direction === "in" || data.direction === "out")
        && (data.target_kind === "PROJECT" || data.target_kind === "PERSONAL")
        && (data.to_key === null || typeof data.to_key === "string") && (data.target_name === null || typeof data.target_name === "string");
}
export interface ChatEventPage { events: ChatEvent[]; cursor: number; has_more: boolean }
export interface ChatAck { message?: ChatMessage; client_message_id?: string; last_read_seq?: number; unread_count?: number }
export type ChatConnectionState = "connecting" | "syncing" | "connected" | "reconnecting" | "unavailable" | "forbidden";
export const CHAT_REACTIONS = ["👍", "❤️", "👀", "✅", "🎉"] as const;
export const chatPath = (projectId: number) => `/api/v1/projects/${projectId}/chat`;
export const chatKeys = {
    root: (projectId: number, userId: number) => ["project-chat", projectId, userId] as const,
    info: (projectId: number, userId: number) => [...chatKeys.root(projectId, userId), "info"] as const,
    messages: (projectId: number, userId: number) => [...chatKeys.root(projectId, userId), "messages"] as const,
    entities: (projectId: number, userId: number) => [...chatKeys.root(projectId, userId), "entities"] as const,
    search: (projectId: number, userId: number) => [...chatKeys.root(projectId, userId), "search"] as const,
};

/** Схлопывает ack/broadcast/retry по автору и UUID, сохраняя серверный порядок. */
export function mergeChatMessages(previous: ChatMessage[], incoming: ChatMessage[]): ChatMessage[] {
    const rows = new Map(previous.map((row) => [`${row.author?.id ?? 0}:${row.client_message_id}`, row]));
    const keysById = new Map(previous.filter((row) => row.id > 0).map((row) => [row.id, `${row.author?.id ?? 0}:${row.client_message_id}`]));
    for (const row of incoming) {
        const key = `${row.author?.id ?? 0}:${row.client_message_id}`;
        const priorKey = row.id > 0 ? keysById.get(row.id) : undefined;
        const current = rows.get(key) ?? (priorKey ? rows.get(priorKey) : undefined);
        if (current && !current.delivery && row.delivery) continue;
        if (current && current.revision > row.revision) continue;
        if (current?.eventSeq && row.eventSeq && current.eventSeq > row.eventSeq) continue;
        // Запоздалый ack не откатывает уже применённое событие реакции той же версии.
        if (current?.eventSeq && row.eventSeq === undefined && !row.delivery && current.revision >= row.revision) continue;
        if (priorKey && priorKey !== key) rows.delete(priorKey);
        rows.set(key, { ...row, eventSeq: row.eventSeq ?? current?.eventSeq });
        if (row.id > 0) keysById.set(row.id, key);
    }
    return [...rows.values()].sort((a, b) => (a.delivery ? Number.MAX_SAFE_INTEGER : a.seq) - (b.delivery ? Number.MAX_SAFE_INTEGER : b.seq) || a.created_at.localeCompare(b.created_at));
}

/** Обновляет и саму реплику, и previews ответов на неё. */
export function applyChatMessage(history: ChatHistory | undefined, message: ChatMessage): ChatHistory {
    const messages = mergeChatMessages(history?.messages ?? [], [message]).map((row) => row.reply?.id === message.id ? {
        ...row, reply: { id: message.id, seq: message.seq, author_type: message.author_type, author: message.author, content: message.deleted_at ? "Сообщение удалено" : message.content.slice(0, 240), deleted: Boolean(message.deleted_at) },
    } : row);
    return { messages, has_more: history?.has_more ?? false, event_cursor: Math.max(history?.event_cursor ?? 0, message.eventSeq ?? 0), initialized: history?.initialized ?? false };
}

/** Сообщение Ариса в чате проекта. */
export const isAgentMessage = (message: Pick<ChatMessage, "author_type">): boolean => message.author_type === "AGENT";
/** Арис ещё готовит ответ — показываем живой статус или «Арис думает…». */
export const isAgentThinking = (message: ChatMessage): boolean =>
    isAgentMessage(message) && (message.agent?.status === "queued" || message.agent?.status === "processing") && !message.content;
/** Имя автора для подписи, уведомления и цитаты. */
export function chatAuthorName(message: { author_type?: ChatAuthorType; author: ChatUser | null }, fallback = "Участник"): string {
    if (message.author_type === "AGENT") return ARIS_NAME;
    return message.author?.display_name.trim() || message.author?.username || fallback;
}
