import { createContext, useContext } from "react";
import type { ChatEntity, ChatMessage } from "./projectChat";
import type { DocumentFragment, KnowledgeSource } from "./types";

export type ChatContextTarget =
    | { type: "task"; id: number; title: string; excerpt?: string | null }
    | { type: "document"; documentRef: string; title: string; fragment?: DocumentFragment | null };

export const CHAT_CONTEXT_LABELS = { task: "Задача", document: "Документ" };
export const chatContextKey = (target: ChatContextTarget) => `${target.type}:${target.type === "document" ? target.documentRef : target.id}`;
const validId = (id: number | null): id is number => id !== null && Number.isSafeInteger(id) && id > 0;
const TASK_SOURCE_TYPES = new Set(["task", "comment", "task_report", "activity"]);
const DOCUMENT_SOURCE_TYPES = new Set(["document", "project_file", "attachment"]);

/** IDs come from server-hydrated cards, never from generated prose. */
export function sourceContextTarget(source: KnowledgeSource): ChatContextTarget | null {
    if (TASK_SOURCE_TYPES.has(source.entity_type)) {
        // Related sources carry their own entity_id; only the server's task_id identifies the task.
        const id = source.entity_type === "task" ? source.task_id ?? source.entity_id : source.task_id;
        return validId(id) ? { type: "task", id, title: source.title, excerpt: source.excerpt } : null;
    }
    if (!DOCUMENT_SOURCE_TYPES.has(source.entity_type) || !validId(source.entity_id)) return null;
    if (!source.document_ref) return null;
    return { type: "document", title: source.title, documentRef: source.document_ref, fragment: source.reference };
}

export function entityContextTarget(entity: ChatEntity): ChatContextTarget | null {
    if (!entity.available) return null;
    if (entity.entity_type !== "TASK" && entity.entity_type !== "DOCUMENT") return null;
    if (entity.entity_type === "DOCUMENT") return entity.document_ref ? { type: "document", title: entity.title, documentRef: entity.document_ref } : null;
    return validId(entity.entity_id) ? { type: "task", id: entity.entity_id!, title: entity.title } : null;
}

function messageTargets(message: ChatMessage): ChatContextTarget[] {
    if (message.deleted_at || message.delivery) return [];
    return [
        ...message.entities.map(entityContextTarget),
        ...(message.agent?.sources ?? []).map(sourceContextTarget),
    ].filter((target): target is ChatContextTarget => target !== null);
}

/** Latest server-hydrated objects, deduplicated across messages and card types. */
export function recentChatContext(messages: ChatMessage[], limit = 8): ChatContextTarget[] {
    const unique = new Map<string, ChatContextTarget>();
    for (const message of [...messages].reverse()) {
        for (const target of messageTargets(message)) {
            if (!unique.has(chatContextKey(target))) unique.set(chatContextKey(target), target);
            if (unique.size >= limit) return [...unique.values()];
        }
    }
    return [...unique.values()];
}

/** Only an unambiguous completed answer can suggest a context automatically. */
export function suggestedChatContext(messages: ChatMessage[]): { key: string; target: ChatContextTarget } | null {
    for (const message of [...messages].reverse()) {
        if (message.deleted_at || message.delivery || message.agent?.status !== "completed") continue;
        const targets = new Map<string, ChatContextTarget>();
        const primary = new Map<string, ChatContextTarget>();
        for (const source of message.agent.sources.filter(source => source.is_primary)) {
            const target = sourceContextTarget(source);
            if (target) primary.set(chatContextKey(target), target);
        }
        if (primary.size === 1) return { key: `${message.id}:${message.revision}`, target: [...primary.values()][0] };
        for (const source of message.agent.sources) {
            const target = sourceContextTarget(source);
            if (target) targets.set(chatContextKey(target), target);
        }
        if (!targets.size) for (const entity of message.entities) {
            const target = entityContextTarget(entity);
            if (target?.type === "task") targets.set(chatContextKey(target), target);
        }
        if (targets.size === 1) return { key: `${message.id}:${message.revision}`, target: [...targets.values()][0] };
    }
    return null;
}

export interface ChatContextValue {
    projectId: number;
    projectKey: string;
    desktop: boolean;
    isOpen: boolean;
    target: ChatContextTarget | null;
    selectionKey: string;
    recent: ChatContextTarget[];
    pinned: boolean;
    select: (target: ChatContextTarget, options?: { open?: boolean }) => void;
    show: () => void;
    close: () => void;
    togglePin: () => void;
}

export const ChatContext = createContext<ChatContextValue | null>(null);
/** Optional: shared cards keep their normal navigation outside the full project chat. */
export const useChatContext = () => useContext(ChatContext);
