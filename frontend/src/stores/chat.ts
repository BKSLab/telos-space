import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type { ChatAttachment, ChatEntity, ChatReply } from "@/lib/projectChat";
import type { ChatMessage } from "@/lib/projectChat";
import { newUuid } from "@/lib/uuid";

export interface ChatDraft {
    content: string; mentions: number[]; entities: ChatEntity[]; attachments: ChatAttachment[];
    reply: ChatReply | null; clientMessageId: string;
}
export const emptyChatDraft = (): ChatDraft => ({ content: "", mentions: [], entities: [], attachments: [], reply: null, clientMessageId: newUuid() });
interface ChatUiState {
    drafts: Record<string, ChatDraft>;
    editing: Record<string, ChatMessage | null>;
    busy: Record<string, boolean>;
    setEditing: (key: string, message: ChatMessage | null) => void;
    setBusy: (key: string, active: boolean) => void;
    updateDraft: (key: string, patch: Partial<ChatDraft>) => void;
    clearDraft: (key: string, clientMessageId?: string) => void;
}

/** Только черновики: история сообщений остаётся в TanStack Query. Ключ включает пользователя. */
export const useChatUiStore = create<ChatUiState>()(persist((set) => ({
    drafts: {}, editing: {}, busy: {},
    setEditing: (key, message) => set((state) => ({ editing: { ...state.editing, [key]: message } })),
    setBusy: (key, active) => set((state) => ({ busy: { ...state.busy, [key]: active } })),
    updateDraft: (key, patch) => set((state) => ({ drafts: { ...state.drafts, [key]: { ...(state.drafts[key] ?? emptyChatDraft()), ...patch } } })),
    clearDraft: (key, clientMessageId) => set((state) => { if (clientMessageId && state.drafts[key]?.clientMessageId !== clientMessageId) return state; const drafts = { ...state.drafts }; delete drafts[key]; return { drafts }; }),
}), { name: "project-chat-drafts", storage: createJSONStorage(() => sessionStorage), partialize: (state) => ({ drafts: state.drafts, editing: state.editing }) }));
