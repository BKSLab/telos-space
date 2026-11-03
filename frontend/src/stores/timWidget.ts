import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type { AgentFile } from "@/lib/types";
import { newUuid } from "@/lib/uuid";

interface TimDraft { content: string; files: AgentFile[]; requestId: string }
interface TimWidgetState {
    /** Панель открыта — живёт только в памяти вкладки, после перезагрузки панель закрыта. */
    open: boolean;
    /** Выбранный диалог по пользователю; нет записи — текущий = первый в списке сервера. */
    conversations: Record<number, number>;
    drafts: Record<string, TimDraft>;
    setOpen: (open: boolean) => void;
    select: (userId: number, id: number) => void;
    updateDraft: (key: string, patch: Partial<TimDraft>) => void;
    clearDraft: (key: string, requestId: string) => void;
}

/** Виджет Ариса: черновик и UUID отправки переживают перезагрузку — повтор не создаёт второй вопрос. */
export const useTimWidgetStore = create<TimWidgetState>()(persist((set) => ({
    open: false, conversations: {}, drafts: {},
    setOpen: (open) => set({ open }),
    select: (userId, id) => set((state) => ({ conversations: { ...state.conversations, [userId]: id } })),
    updateDraft: (key, patch) => set((state) => ({ drafts: { ...state.drafts, [key]: {
        ...(state.drafts[key] ?? { content: "", files: [] }), requestId: newUuid(), ...patch,
    } } })),
    clearDraft: (key, requestId) => set((state) => {
        if (state.drafts[key]?.requestId !== requestId) return state;
        const drafts = { ...state.drafts }; delete drafts[key]; return { drafts };
    }),
}), {
    name: "tim-widget",
    storage: createJSONStorage(() => sessionStorage),
    partialize: (state) => ({ conversations: state.conversations, drafts: state.drafts }),
}));
