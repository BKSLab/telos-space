import { createContext, useContext } from "react";
import type { ChatEntity, ChatMessage } from "./projectChat";
import type { KnowledgeSource } from "./types";

export interface ChatContextTarget {
    type: "task" | "document" | "project_file";
    id: number;
    title: string;
    excerpt?: string | null;
    documentSlug?: string | null;
}

export const CHAT_CONTEXT_LABELS = { task: "Задача", document: "Документ", project_file: "Файл проекта" };
export const chatContextKey = (target: ChatContextTarget) => `${target.type}:${target.id}`;
const validId = (id: number) => Number.isSafeInteger(id) && id > 0;

/** IDs come from server-hydrated cards, never from generated prose. */
export function sourceContextTarget(source: KnowledgeSource): ChatContextTarget | null {
    if (!["task", "document", "project_file"].includes(source.entity_type)) return null;
    const id = source.entity_type === "task" ? source.task_id ?? source.entity_id : source.entity_id;
    if (!validId(id)) return null;
    return { type: source.entity_type as ChatContextTarget["type"], id, title: source.title, excerpt: source.excerpt, documentSlug: source.document_slug };
}

export function entityContextTarget(entity: ChatEntity): ChatContextTarget | null {
    if (!entity.available || !validId(entity.entity_id)) return null;
    if (entity.entity_type !== "TASK" && entity.entity_type !== "DOCUMENT") return null;
    return { type: entity.entity_type === "TASK" ? "task" : "document", id: entity.entity_id, title: entity.title };
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
        for (const source of message.agent.sources) {
            const target = sourceContextTarget(source);
            if (target) targets.set(chatContextKey(target), target);
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
