import { useState, type ReactNode } from "react";
import { ChatContext, recentChatContext, suggestedChatContext, type ChatContextTarget } from "@/lib/chatContext";
import type { ChatMessage } from "@/lib/projectChat";
import { useMediaQuery } from "@/lib/useCalendarView";

export function ChatContextProvider({ projectId, projectKey, messages, enabled, children }: {
    projectId: number; projectKey: string; messages: ChatMessage[]; enabled: boolean; children: ReactNode;
}) {
    const desktop = useMediaQuery("(min-width: 1280px)");
    const [desktopOpen, setDesktopOpen] = useState(true);
    const [mobileOpen, setMobileOpen] = useState(false);
    const [pinned, setPinned] = useState(false);
    const [selection, setSelection] = useState<{ target: ChatContextTarget; suggestionKey: string | undefined; refreshKey: string; picks: number } | null>(null);
    const suggestion = suggestedChatContext(messages);
    const automatic = !pinned && suggestion && selection?.suggestionKey !== suggestion.key;
    const target = automatic ? suggestion.target : selection?.target ?? null;
    const selectionKey = automatic ? `answer:${suggestion.key}` : selection?.refreshKey ?? "empty";
    const show = () => { if (desktop) setDesktopOpen(true); else setMobileOpen(true); };
    const close = () => {
        if (desktop) {
            setDesktopOpen(false);
            requestAnimationFrame(() => document.getElementById("project-chat-context-toggle")?.focus());
        } else setMobileOpen(false);
    };
    return <ChatContext.Provider value={enabled ? {
        projectId, projectKey, desktop, isOpen: desktop ? desktopOpen : mobileOpen, target, selectionKey,
        recent: recentChatContext(messages), pinned, show, close,
        select: (next, { open = true } = {}) => {
            setSelection((previous) => ({ target: next, suggestionKey: suggestion?.key, picks: (previous?.picks ?? 0) + 1, refreshKey: `pick:${(previous?.picks ?? 0) + 1}` }));
            if (open) show();
        },
        togglePin: () => {
            if (!target) return;
            if (!pinned) setSelection((previous) => ({ target, suggestionKey: suggestion?.key, refreshKey: selectionKey, picks: previous?.picks ?? 0 }));
            setPinned(!pinned);
        },
    } : null}>{children}</ChatContext.Provider>;
}
