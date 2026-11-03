import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

export type ChatPanel = "team" | "personal";
interface ChatDockState {
    projects: Record<number, number>;
    modes: Record<number, ChatPanel>;
    panel: ChatPanel | null;
    selectProject: (userId: number, projectId: number) => void;
    selectPersonal: (userId: number) => void;
    open: (panel: ChatPanel | null) => void;
}
export const useChatDockStore = create<ChatDockState>()(persist((set) => ({
    projects: {}, modes: {}, panel: null,
    selectProject: (userId, projectId) => set((state) => ({ projects: { ...state.projects, [userId]: projectId }, modes: { ...state.modes, [userId]: "team" } })),
    selectPersonal: userId => set(state => ({ modes: { ...state.modes, [userId]: "personal" } })),
    open: (panel) => set({ panel }),
}), { name: "project-chat-dock", storage: createJSONStorage(() => sessionStorage), partialize: (state) => ({ projects: state.projects, modes: state.modes }) }));
