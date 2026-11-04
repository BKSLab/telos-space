import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { BoardObjectSelectionRef } from "@/lib/types";

export interface BoardSelectionContext {
    boardId: number;
    objects: BoardObjectSelectionRef[];
}

export type WbsLayoutMode = "horizontal" | "vertical";

interface UiState {
    boardSelection: BoardSelectionContext | null;
    setBoardSelection: (selection: BoardSelectionContext | null) => void;
    /** Открытая карточка задачи — общая для канбана, списка и структуры. */
    selectedTaskId: number | null;
    setSelectedTaskId: (taskId: number | null) => void;
    selectedRisk: { projectId: number; riskId: number } | null;
    setSelectedRisk: (risk: { projectId: number; riskId: number } | null) => void;

    sidebarCollapsed: boolean;
    toggleSidebar: () => void;

    projectListCollapsed: boolean;
    toggleProjectList: () => void;
    portfolioView: "projects" | "summary";
    setPortfolioView: (view: "projects" | "summary") => void;

    /** Свёрнутые ветки ИСР — локальное состояние вида, на backend не уходит. */
    collapsedWbsNodes: Set<number>;
    toggleWbsNode: (nodeId: number) => void;
    expandWbsNodes: (nodeIds: number[]) => void;

    wbsLayoutMode: WbsLayoutMode;
    setWbsLayoutMode: (mode: WbsLayoutMode) => void;
}

export const useUiStore = create<UiState>()(persist((set) => ({
    boardSelection: null,
    setBoardSelection: (selection) => set((state) => {
        const previous = state.boardSelection;
        if (previous === selection || (previous && selection && previous.boardId === selection.boardId
            && previous.objects.length === selection.objects.length
            && previous.objects.every((item, index) => item.type === selection.objects[index].type && item.id === selection.objects[index].id))) return state;
        return { boardSelection: selection };
    }),
    selectedTaskId: null,
    setSelectedTaskId: (taskId) => set(taskId === null ? { selectedTaskId: null } : { selectedTaskId: taskId, selectedRisk: null }),
    selectedRisk: null,
    setSelectedRisk: (risk) => set(risk === null ? { selectedRisk: null } : { selectedRisk: risk, selectedTaskId: null }),

    sidebarCollapsed: false,
    toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),

    projectListCollapsed: false,
    toggleProjectList: () => set((state) => ({ projectListCollapsed: !state.projectListCollapsed })),
    portfolioView: "projects",
    setPortfolioView: (portfolioView) => set({ portfolioView }),

    collapsedWbsNodes: new Set<number>(),
    toggleWbsNode: (nodeId) =>
        set((state) => {
            const next = new Set(state.collapsedWbsNodes);
            if (next.has(nodeId)) {
                next.delete(nodeId);
            } else {
                next.add(nodeId);
            }
            return { collapsedWbsNodes: next };
        }),
    expandWbsNodes: (nodeIds) =>
        set((state) => {
            const next = new Set(state.collapsedWbsNodes);
            for (const nodeId of nodeIds) {
                next.delete(nodeId);
            }
            return { collapsedWbsNodes: next };
        }),

    // ИСР почти всегда рисуют сверху вниз, поэтому это и режим по умолчанию.
    wbsLayoutMode: "vertical",
    setWbsLayoutMode: (mode) => set({ wbsLayoutMode: mode }),
}), {
    name: "telos-workspace-preferences",
    // Only display preferences are saved; task and board state stays in memory.
    partialize: ({ projectListCollapsed, portfolioView }) => ({ projectListCollapsed, portfolioView }),
}));
