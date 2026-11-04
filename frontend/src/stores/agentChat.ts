import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type { AgentFile } from "@/lib/types";
import { newUuid } from "@/lib/uuid";

interface AgentDraft { content: string; files: AgentFile[]; requestId: string }
interface AgentChatState {
    conversations: Record<string, number>;
    drafts: Record<string, AgentDraft>;
    select: (key: string, id: number) => void;
    updateDraft: (key: string, patch: Partial<AgentDraft>) => void;
    clearDraft: (key: string, requestId: string) => void;
}

/** Вкладка и окно используют один черновик; ключ включает пользователя и проект. */
export const useAgentChatStore = create<AgentChatState>()(persist((set) => ({
    conversations: {}, drafts: {},
    select: (key, id) => set((state) => ({ conversations: { ...state.conversations, [key]: id } })),
    updateDraft: (key, patch) => set((state) => ({ drafts: { ...state.drafts, [key]: {
        ...(state.drafts[key] ?? { content: "", files: [] }), requestId: newUuid(), ...patch,
    } } })),
    clearDraft: (key, requestId) => set((state) => {
        if (state.drafts[key]?.requestId !== requestId) return state;
        const drafts = { ...state.drafts }; delete drafts[key]; return { drafts };
    }),
}), { name: "project-agent-drafts", storage: createJSONStorage(() => sessionStorage) }));
