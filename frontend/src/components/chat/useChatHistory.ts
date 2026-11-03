import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

/** Держит читаемое сообщение на месте при подгрузке истории и изменении высоты ответа. */
export function useChatHistory(session: string, contentVersion: string) {
    const viewport = useRef<HTMLDivElement>(null);
    const content = useRef<HTMLDivElement>(null);
    const previous = useRef({ session, first: "" });
    const initialized = useRef(false);
    const bottom = useRef(true);
    const anchor = useRef<{ id: string; offset: number } | null>(null);
    const [atBottom, setAtBottom] = useState(true);

    const capture = useCallback(() => {
        const root = viewport.current;
        if (!root) return;
        const top = root.getBoundingClientRect().top;
        const row = [...root.querySelectorAll<HTMLElement>("[data-chat-message]")].find(item => item.getBoundingClientRect().bottom > top);
        if (row) anchor.current = { id: row.dataset.chatMessage!, offset: row.getBoundingClientRect().top - top };
    }, []);
    const restore = useCallback(() => {
        const root = viewport.current;
        const saved = anchor.current;
        if (!root || !saved) return;
        const row = [...root.querySelectorAll<HTMLElement>("[data-chat-message]")].find(item => item.dataset.chatMessage === saved.id);
        if (row) root.scrollTop += row.getBoundingClientRect().top - root.getBoundingClientRect().top - saved.offset;
    }, []);
    const scrollToLatest = useCallback(() => {
        if (viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
        bottom.current = true;
        setAtBottom(true);
        capture();
    }, [capture]);
    const onScroll = useCallback(() => {
        const root = viewport.current;
        if (!root) return;
        bottom.current = root.scrollHeight - root.scrollTop - root.clientHeight < 56;
        setAtBottom(bottom.current);
        capture();
    }, [capture]);

    useLayoutEffect(() => {
        const root = viewport.current;
        if (!root) return;
        if (previous.current.session !== session) {
            initialized.current = false;
            bottom.current = true;
            anchor.current = null;
            setAtBottom(true);
            previous.current = { session, first: "" };
        }
        const first = root.querySelector<HTMLElement>("[data-chat-message]")?.dataset.chatMessage ?? "";
        if (!first) return;
        const prepended = initialized.current && previous.current.first !== first;
        if (!initialized.current) { root.scrollTop = root.scrollHeight; initialized.current = true; }
        else if (prepended || !bottom.current) restore();
        else root.scrollTop = root.scrollHeight;
        previous.current = { session, first };
        onScroll();
    }, [session, contentVersion, onScroll, restore]);

    useEffect(() => {
        const root = viewport.current;
        const rows = content.current;
        if (!root || !rows || typeof ResizeObserver === "undefined") return;
        const observer = new ResizeObserver(() => {
            if (!initialized.current) return;
            if (bottom.current) root.scrollTop = root.scrollHeight;
            else restore();
            capture();
        });
        observer.observe(root);
        observer.observe(rows);
        return () => observer.disconnect();
    }, [capture, restore]);

    return { viewportRef: viewport, contentRef: content, atBottom, onScroll, scrollToLatest };
}
