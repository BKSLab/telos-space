import { Fragment, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowDown, Loader2, MessageSquare } from "lucide-react";
import { chatAuthorName, isAgentThinking, type ChatMessage } from "@/lib/projectChat";
import { useProjectChat } from "@/lib/useProjectChat";
import { ChatMessage as MessageRow } from "@/components/chat/ChatMessage";
import { Button } from "@/components/ui/Button";

interface Props {
    messages: ChatMessage[]; hasMore: boolean; loadOlder: () => Promise<void>; initialReadSeq: number;
    highlightedId?: number; onReply: (message: ChatMessage) => void; onQuote: (text: string) => void;
    onEdit: (message: ChatMessage) => void; onJump: (id: number) => void;
}

export function ChatMessageList({ messages, hasMore, loadOlder, initialReadSeq, highlightedId, onReply, onQuote, onEdit, onJump }: Props) {
    const { markRead, state, userId } = useProjectChat();
    const viewport = useRef<HTMLDivElement>(null);
    const content = useRef<HTMLDivElement>(null);
    const top = useRef<HTMLDivElement>(null);
    const initialized = useRef(false);
    const anchor = useRef<{ id: string; offset: number } | null>(null);
    const wasBottom = useRef(true);
    const loading = useRef(false);
    const [loadingOlder, setLoadingOlder] = useState(false);
    const [olderError, setOlderError] = useState<string | null>(null);
    const [atBottom, setAtBottom] = useState(true);
    const [seenTail, setSeenTail] = useState(0);
    const tail = messages.filter((message) => !message.delivery).at(-1)?.seq ?? 0;

    const captureAnchor = useCallback(() => {
        const root = viewport.current;
        if (!root) return;
        const bounds = root.getBoundingClientRect();
        const row = [...root.querySelectorAll<HTMLElement>("[data-message-id]")].find((element) => element.getBoundingClientRect().bottom > bounds.top);
        if (row) anchor.current = { id: row.id, offset: row.getBoundingClientRect().top - bounds.top };
    }, []);
    const older = useCallback(async () => {
        if (loading.current || !hasMore) return;
        loading.current = true; setLoadingOlder(true); setOlderError(null); captureAnchor();
        try { await loadOlder(); } catch (error) { setOlderError((error as Error).message); } finally { loading.current = false; setLoadingOlder(false); }
    }, [captureAnchor, hasMore, loadOlder]);

    useLayoutEffect(() => {
        const root = viewport.current;
        if (!root || !messages.length) return;
        if (!initialized.current) {
            initialized.current = true;
            const highlighted = highlightedId && root.querySelector<HTMLElement>(`[data-message-id="${highlightedId}"]`);
            if (highlighted) highlighted.scrollIntoView({ block: "center" });
            else root.scrollTop = root.scrollHeight;
        } else if (wasBottom.current && !loading.current) root.scrollTop = root.scrollHeight;
        else if (anchor.current) {
            const element = document.getElementById(anchor.current.id);
            if (element && root.contains(element)) root.scrollTop += element.getBoundingClientRect().top - root.getBoundingClientRect().top - anchor.current.offset;
        }
        captureAnchor();
    }, [messages, highlightedId, captureAnchor]);

    useEffect(() => {
        const root = viewport.current, element = content.current;
        if (!root || !element) return;
        const resize = new ResizeObserver(() => {
            if (!initialized.current) return;
            if (wasBottom.current && !loading.current) root.scrollTop = root.scrollHeight;
            else if (anchor.current) {
                const current = document.getElementById(anchor.current.id);
                if (current && root.contains(current)) root.scrollTop += current.getBoundingClientRect().top - root.getBoundingClientRect().top - anchor.current.offset;
            }
        });
        resize.observe(element); resize.observe(root);
        return () => resize.disconnect();
    }, []);

    useEffect(() => {
        const sentinel = top.current;
        if (!sentinel || !hasMore || !initialized.current) return;
        const observer = new IntersectionObserver((entries) => { if (entries.some((entry) => entry.isIntersecting)) void older(); }, { root: viewport.current, rootMargin: "100px 0px 0px" });
        observer.observe(sentinel);
        return () => observer.disconnect();
    }, [hasMore, older, messages.length]);

    useEffect(() => {
        const root = viewport.current;
        if (!root || state !== "connected") return;
        let timer: ReturnType<typeof setTimeout> | undefined;
        const visible = new Set<number>();
        const schedule = () => {
            clearTimeout(timer);
            if (document.hidden || !document.hasFocus()) return;
            timer = setTimeout(() => {
                const last = messages.filter((message) => !message.delivery && visible.has(message.id)).at(-1);
                if (last && !document.hidden && document.hasFocus()) void markRead(last).catch(() => { /* После reconnect отметим видимые строки заново. */ });
            }, 650);
        };
        const observer = new IntersectionObserver((entries) => {
            for (const entry of entries) {
                const id = Number((entry.target as HTMLElement).dataset.messageId);
                if (entry.isIntersecting) visible.add(id); else visible.delete(id);
            }
            schedule();
        }, { root, threshold: 0.6 });
        root.querySelectorAll("[data-message-seq]").forEach((row) => observer.observe(row));
        window.addEventListener("focus", schedule);
        document.addEventListener("visibilitychange", schedule);
        return () => { clearTimeout(timer); observer.disconnect(); window.removeEventListener("focus", schedule); document.removeEventListener("visibilitychange", schedule); };
    }, [markRead, messages, state]);

    const scroll = () => {
        const root = viewport.current;
        if (!root) return;
        const bottom = root.scrollHeight - root.scrollTop - root.clientHeight < 70;
        wasBottom.current = bottom; setAtBottom(bottom);
        if (bottom) setSeenTail(tail);
        captureAnchor();
    };
    const unreadIndex = messages.findIndex((message) => !message.delivery && !message.deleted_at && message.author?.id !== userId && message.seq > initialReadSeq);
    const newCount = messages.filter((message) => !message.delivery && message.seq > seenTail).length;
    const latestIncoming = messages.filter((message) => !message.delivery && !message.deleted_at && !isAgentThinking(message) && message.author?.id !== userId).at(-1);
    const day = (value: string) => new Date(value).toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
    return <div className="relative min-h-0 flex-1">
        <span className="sr-only" aria-live="polite" aria-atomic="true">{latestIncoming && `${chatAuthorName(latestIncoming)}: ${latestIncoming.content.slice(0, 160) || "Вложение"}`}</span>
        <div ref={viewport} onScroll={scroll} aria-label="История сообщений проекта" className="chat-history h-full overflow-y-auto overscroll-contain [overflow-anchor:none]">
            <div ref={content} className="mx-auto w-full max-w-[var(--chat-content-width,56rem)] px-2 pt-2 pb-6 sm:px-4">
                <div ref={top} className="flex min-h-5 justify-center">{hasMore && <Button variant="ghost" size="sm" disabled={loadingOlder} onClick={() => void older()} icon={loadingOlder ? <Loader2 size={12} className="animate-spin motion-reduce:animate-none" /> : undefined}>{loadingOlder ? "Загрузка…" : "Ранние сообщения"}</Button>}</div>
                {olderError && <p role="alert" className="py-2 text-center text-xs text-danger">{olderError}</p>}
                {!messages.length && <div className="flex min-h-64 flex-col items-center justify-center gap-3 text-center"><MessageSquare size={26} className="text-muted/60" /><p className="text-sm text-secondary">Начните разговор с командой</p><p className="max-w-sm text-xs leading-relaxed text-muted">Здесь можно обсудить проект, сослаться на задачу и сохранить общие договорённости.</p></div>}
                {messages.map((message, index) => <Fragment key={`${message.author?.id}:${message.client_message_id}`}>
                    {(index === 0 || day(messages[index - 1].created_at) !== day(message.created_at)) && <div className="my-4 flex items-center gap-3 px-3 text-[10px] text-muted"><span className="h-px flex-1 bg-line-subtle" /><span>{day(message.created_at)}</span><span className="h-px flex-1 bg-line-subtle" /></div>}
                    {index === unreadIndex && <div className="my-2 flex items-center gap-3 px-3 text-[10px] text-accent" role="separator" aria-label="Непрочитанные сообщения"><span className="h-px flex-1 bg-accent/20" />Непрочитанное<span className="h-px flex-1 bg-accent/20" /></div>}
                    <MessageRow message={message} highlighted={message.id === highlightedId} onReply={onReply} onQuote={onQuote} onEdit={onEdit} onJump={onJump} />
                </Fragment>)}
            </div>
        </div>
        {!atBottom && <div className="absolute right-5 bottom-3"><Button size="sm" className="material-glass shadow-panel" icon={<ArrowDown size={13} />} onClick={() => { if (viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight; }}>{newCount > 0 ? `Новые сообщения · ${newCount}` : "Вниз"}</Button></div>}
    </div>;
}
