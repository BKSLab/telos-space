import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ArrowDown, ArrowRightLeft, Search, Users, Wifi, WifiOff, X } from "lucide-react";
import { ApiError } from "@/lib/api";
import { useProjectChat } from "@/lib/useProjectChat";
import { useChatMessages } from "@/lib/useChatMessages";
import { useChatUiStore } from "@/stores/chat";
import type { ChatMessage } from "@/lib/projectChat";
import { ChatMessageList } from "@/components/chat/ChatMessageList";
import { ChatComposer } from "@/components/chat/ChatComposer";
import { ChatSearch } from "@/components/chat/ChatSearch";
import { Button, IconButton } from "@/components/ui/Button";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";
import { Popover } from "@/components/ui/Popover";
import { cn } from "@/lib/cn";

const STATUS = { connecting: "Подключение…", syncing: "Восстановление сообщений…", connected: "Подключено", reconnecting: "Восстанавливаем соединение…", unavailable: "Нет соединения", forbidden: "Доступ к чату закрыт" };

export function ProjectChatWorkspace({ compact = false }: { compact?: boolean }) {
    const chat = useProjectChat();
    if (chat.loading) return <div className="p-5"><Skeleton className="h-20 w-full" /><Skeleton className="mt-4 h-64 w-full" /></div>;
    if (chat.error) return <div className="p-5">{chat.error instanceof ApiError && chat.error.status === 404 ? <EmptyState title="Чат недоступен" description="Общий чат создаётся вместе с новым проектом и доступен его участникам." /> : <ErrorMessage message={chat.error.message} />}</div>;
    if (!chat.info || chat.state === "forbidden") return <div className="p-5"><EmptyState title="Доступ к чату закрыт" description="Для просмотра переписки нужно участвовать в проекте." /></div>;
    return <ProjectChatContent key={`${chat.projectId}:${chat.userId}`} compact={compact} />;
}

function ProjectChatContent({ compact }: { compact: boolean }) {
    const chat = useProjectChat();
    const [params, setParams] = useSearchParams();
    const [windowMessage, setWindowMessage] = useState<number>();
    const candidate = compact ? windowMessage : Number(params.get("message"));
    const around = candidate != null && Number.isSafeInteger(candidate) && candidate > 0 ? candidate : undefined;
    const messages = useChatMessages(chat.projectId, chat.userId, Boolean(chat.info), around);
    const [searching, setSearching] = useState(false);
    const draftKey = `${chat.userId}:${chat.projectId}`;
    const editing = useChatUiStore((state) => state.editing[draftKey]) ?? null;
    const changeEditing = useChatUiStore((state) => state.setEditing);
    const setEditing = (message: ChatMessage | null) => {
        if (editing) useChatUiStore.getState().clearDraft(`${draftKey}:edit:${editing.id}:${editing.revision}`);
        changeEditing(draftKey, message);
    };
    const [initialReadSeq] = useState(chat.info?.last_read_seq ?? 0);
    const updateDraft = useChatUiStore((state) => state.updateDraft);
    const showMessage = (id?: number) => {
        if (compact) setWindowMessage(id);
        else setParams((previous) => { const next = new URLSearchParams(previous); if (id) next.set("message", String(id)); else next.delete("message"); return next; });
    };
    const jump = (id: number) => {
        setSearching(false);
        const existing = document.getElementById(`chat-message-${id}`);
        if (existing) { existing.scrollIntoView({ block: "center" }); existing.focus({ preventScroll: true }); }
        else showMessage(id);
    };
    const reply = (message: ChatMessage) => {
        setEditing(null);
        updateDraft(draftKey, { reply: { id: message.id, seq: message.seq, author_type: message.author_type, author: message.author, content: message.content.slice(0, 240), deleted: Boolean(message.deleted_at) }, clientMessageId: crypto.randomUUID() });
        requestAnimationFrame(() => document.getElementById("project-chat-composer")?.focus());
    };
    const quote = (text: string) => {
        setEditing(null);
        const previous = useChatUiStore.getState().drafts[draftKey]?.content ?? "";
        updateDraft(draftKey, { content: `${previous}${previous ? "\n" : ""}${text.split("\n").map((line) => `> ${line}`).join("\n")}\n\n`.slice(0, 8000), clientMessageId: crypto.randomUUID() });
        requestAnimationFrame(() => document.getElementById("project-chat-composer")?.focus());
    };
    const onlineCount = chat.info?.members.filter((member) => chat.online.includes(member.id)).length ?? 0;
    return <div className={cn("material-mineral flex h-full min-h-0 flex-col", !compact && "[--chat-content-width:100%]")} aria-label="Общий чат проекта">
        <header className="chat-project-header material-metal flex shrink-0 items-center gap-3 border-b border-line px-3 py-2 sm:px-6 sm:py-4">
            <div className="min-w-0 flex-1"><h2 className="text-base font-semibold tracking-tight text-primary">Чат проекта</h2><p className="chat-project-subtitle mt-1 hidden text-[12px] leading-relaxed text-secondary sm:block">Обсуждения и договорённости команды</p></div>
            <Popover label="Участники чата" trigger={<span className="flex items-center gap-1.5 text-xs"><Users size={14} /><span>{chat.info?.members.length}</span><span className="hidden text-muted sm:inline">· {onlineCount} в сети</span></span>} width={280}>
                <div className="max-h-72 overflow-y-auto p-2"><p className="mb-2 px-1 text-[10px] text-muted">Участники проекта</p>{chat.info?.members.map((member) => <div key={member.id} className="flex items-center gap-2 rounded-md px-1 py-2.5 text-xs text-secondary"><span className={cn("size-1.5 shrink-0 rounded-full", chat.online.includes(member.id) ? "bg-success" : "bg-muted/30")} /><span className="min-w-0 flex-1 break-words">{member.display_name}</span><span className="shrink-0 text-[10px] text-muted">{chat.online.includes(member.id) ? "в сети" : "не в сети"}</span></div>)}</div>
            </Popover>
            <IconButton label="Поиск в чате" onClick={() => setSearching((value) => !value)}><Search size={16} /></IconButton>
        </header>
        <div data-connected={chat.state === "connected"} className={cn("chat-connection-status flex min-h-7 shrink-0 items-center justify-center gap-1.5 border-b border-line-subtle px-3 py-1 text-[12px]", chat.state === "connected" ? "bg-surface text-muted" : "bg-warning/5 text-warning")} role="status" aria-live="polite">{chat.state === "connected" ? <Wifi size={11} aria-hidden="true" /> : <WifiOff size={11} aria-hidden="true" />}{STATUS[chat.state]}{chat.state === "reconnecting" && " Черновик сохранён."}</div>
        {chat.taskMoves.length > 0 && <ul aria-label="Переносы задач" aria-live="polite" className="shrink-0 border-b border-line bg-surface px-4 py-2 sm:px-6">{chat.taskMoves.map((move) => <li key={move.seq} className="flex items-center gap-2 py-1 text-[11px] text-secondary">
            <ArrowRightLeft size={12} className="shrink-0 text-muted" aria-hidden="true" /><span className="min-w-0 flex-1">{move.text}</span>
            <IconButton label="Скрыть уведомление о переносе" size="sm" onClick={() => chat.dismissTaskMove(move.seq)}><X size={12} aria-hidden="true" /></IconButton>
        </li>)}</ul>}
        <div className="relative flex min-h-0 flex-1">
            <div className="flex min-w-0 flex-1 flex-col">
                {around && <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line bg-surface px-4 py-2 text-xs text-secondary sm:px-6"><span>Контекст сообщения</span><Button size="sm" variant="ghost" icon={<ArrowDown size={12} />} onClick={() => showMessage()}>К последним сообщениям</Button></div>}
                {messages.isPending || !messages.data?.initialized ? <div className="flex-1 p-5">{messages.error ? <ErrorMessage message={messages.error.message} /> : <Skeleton className="h-48 w-full" />}</div> : <ChatMessageList key={around ?? "latest"} messages={messages.data.messages} hasMore={messages.data.has_more} loadOlder={messages.loadOlder} initialReadSeq={initialReadSeq} highlightedId={around} onReply={reply} onQuote={quote} onEdit={setEditing} onJump={jump} />}
                <ChatComposer key={editing ? `edit-${editing.id}-${editing.revision}` : "new"} editing={editing} onCancelEdit={() => setEditing(null)} />
            </div>
            {searching && <ChatSearch compact={compact} onJump={jump} onClose={() => setSearching(false)} />}
        </div>
    </div>;
}
