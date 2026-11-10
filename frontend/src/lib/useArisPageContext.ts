import { useMemo } from "react";
import { useLocation } from "react-router-dom";
import { pageContext } from "@/lib/aris";
import type { AgentPageContext } from "@/lib/types";
import { useUiStore } from "@/stores/ui";

/** Проверяемый сервером контекст текущей страницы и выделения доски. */
export function useArisPageContext(): AgentPageContext {
    const location = useLocation();
    const selectedTaskId = useUiStore((state) => state.selectedTaskId);
    const selection = useUiStore((state) => state.boardSelection);
    return useMemo(() => pageContext(location.pathname, location.search, selectedTaskId, selection),
        [location.pathname, location.search, selectedTaskId, selection]);
}
