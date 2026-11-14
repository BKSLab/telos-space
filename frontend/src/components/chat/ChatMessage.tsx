import { ArisChatReply } from "@/components/chat/ArisChatReply";
import { useRef, useState } from "react";
import { Check, CornerUpLeft, MoreHorizontal, Pencil, Quote, RotateCw, SmilePlus, Trash2 } from "lucide-react";
import { CHAT_REACTIONS, chatAuthorName, chatEntityKey, type ChatMessage as Message } from "@/lib/projectChat";
import { useProjectChat } from "@/lib/useProjectChat";
import { cn } from "@/lib/cn";
import { Button } from "@/components/ui/Button";
import { Popover } from "@/components/ui/Popover";
import { Modal } from "@/components/ui/Modal";
import { ChatEntityCard } from "@/components/chat/ChatEntityCard";
import { ChatAttachment } from "@/components/chat/ChatAttachment";
import { ChatText } from "@/components/chat/ChatText";
import { ArisAvatar } from "@/components/brand/ArisAvatar";

interface Props { message: Message; highlighted?: boolean; onReply: (message: Message) => void; onQuote: (text: string) => void; onEdit: (message: Message) => void; onJump: (id: number) => void }

export function ChatMessage({ message, highlighted, onReply, onQuote, onEdit, onJump }: Props) {
    const { projectId, userId, canWrite, command, send, state } = useProjectChat();
    const own = message.author?.id === userId;
    const [error, setError] = useState<string | null>(null);
    const [deleting, setDeleting] = useState(false);
    const [working, setWorking] = useState(false);
    const body = useRef<HTMLDivElement>(null);
    const selectedQuote = useRef("");
    const captureSelection = () => {
        const selection = window.getSelection();
        selectedQuote.current = selection?.anchorNode && selection.focusNode && body.current?.contains(selection.anchorNode) && body.current.contains(selection.focusNode) ? selection.toString() : "";
    };
    const run = async (operation: () => Promise<unknown>) => {
        setError(null); setWorking(true);
        try { await operation(); setDeleting(false); } catch (failure) { setError((failure as Error).message); } finally { setWorking(false); }
    };
    const quote = () => {
        onQuote((selectedQuote.current || message.content).slice(0, 3000));
        selectedQuote.current = "";
    };
    const interactive = canWrite && !message.deleted_at && !message.delivery;
    const initials = chatAuthorName(message, "?").split(" ").slice(0, 2).map((part) => part[0]).join("");
    return <article id={`chat-message-${message.id}`} data-message-id={message.id} data-message-seq={message.delivery ? undefined : message.seq} tabIndex={-1} aria-label={`Сообщение ${message.author_type === "AGENT" ? "Ариса" : chatAuthorName(message, "удалённого пользователя")}`} className={cn("group relative my-1 flex gap-3 rounded-xl px-3 py-4 text-primary outline-none transition-colors focus-visible:ring-2 focus-visible:ring-accent sm:gap-4 sm:px-4", highlighted ? "bg-accent/[0.07]" : "hover:bg-control", message.delivery === "failed" && "bg-danger/[0.03]") }>
        {message.author_type === "AGENT" ? <ArisAvatar className="mt-0.5" /> : <div aria-hidden="true" className={cn("material-metal mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg border border-line text-[10px] font-semibold", own ? "text-accent" : "text-secondary")}>{initials}</div>}
        <div className="min-w-0 flex-1">
            <div className={cn("mb-1.5 flex min-h-5 flex-wrap items-baseline gap-x-2 gap-y-0.5", interactive && "chat-message-meta pr-16")}>
                <span className={cn("max-w-full text-[12px] font-semibold [overflow-wrap:anywhere]", message.author_type === "AGENT" ? "text-ai-blue" : "text-primary")}>{chatAuthorName(message, "Удалённый пользователь")}</span>
                {own && <span className="text-[10px] text-muted">вы</span>}
                <time dateTime={message.created_at} title={new Date(message.created_at).toLocaleString("ru-RU")} className="font-mono text-[10px] text-muted">{new Date(message.created_at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}</time>
                {message.edited_at && !message.deleted_at && <span className="text-[10px] text-muted">изменено</span>}
                {own && !message.delivery && <Check size={11} className="text-muted" aria-label="Отправлено" />}
            </div>
            {message.reply && <button type="button" onClick={() => onJump(message.reply!.id)} className="mb-3 flex max-w-full items-start gap-2 rounded-r-md border-l-2 border-accent/35 bg-control py-2 pr-3 pl-3 text-left text-xs leading-relaxed text-secondary transition-colors hover:bg-hover hover:text-primary focus-visible:outline-2 focus-visible:outline-accent" aria-label="Перейти к исходному сообщению">
                <CornerUpLeft size={12} className="mt-0.5 shrink-0 text-muted" aria-hidden="true" /><span className="min-w-0"><span className="block text-[10px] font-medium text-muted">{chatAuthorName(message.reply)}</span><span className="line-clamp-2">{message.reply.deleted ? "Сообщение удалено" : message.reply.content || "Вложение"}</span></span>
            </button>}
            {message.deleted_at ? <p className="text-xs italic text-muted">Сообщение удалено</p> : <>
                <div ref={body} className="chat-content">{message.author_type === "AGENT" ? <ArisChatReply message={message} /> : <ChatText content={message.content} />}</div>
                {!!message.mentions.length && <div className="mt-1 flex flex-wrap gap-1.5">{message.mentions.map((member) => <span key={member.id} className={cn("text-[11px]", member.id === userId ? "rounded bg-accent/10 px-1 text-accent" : "text-secondary")}>@{member.display_name}</span>)}</div>}
                {!!message.entities.length && <div className="mt-3 flex flex-wrap gap-3">{message.entities.map((entity) => <ChatEntityCard key={chatEntityKey(entity)} entity={entity} projectId={projectId} />)}</div>}
                {!!message.attachments.length && <div className="mt-3 flex min-w-0 flex-wrap gap-3">{message.attachments.map((file) => <ChatAttachment key={file.id} file={file} projectId={projectId} />)}</div>}
                {!!message.reactions.length && <div className="mt-3 flex flex-wrap gap-1.5">{message.reactions.map((reaction) => <button key={reaction.reaction} type="button" disabled={!canWrite || working || state !== "connected"} aria-label={`${reaction.reaction}: ${reaction.user_ids.length}`} aria-pressed={reaction.user_ids.includes(userId)} onClick={() => void run(() => command("reaction.set", { reaction: reaction.reaction, active: !reaction.user_ids.includes(userId) }, message.id))} className={cn("chat-quick-reaction rounded-md border px-2 py-1 text-xs transition-colors focus-visible:outline-2 focus-visible:outline-accent", reaction.user_ids.includes(userId) ? "border-accent/30 bg-accent/10 text-accent" : "border-line bg-control text-secondary hover:bg-hover")}>{reaction.reaction} <span className="ml-0.5 text-[10px]">{reaction.user_ids.length}</span></button>)}</div>}
            </>}
            {message.delivery === "sending" && <p className="mt-1 text-[10px] text-muted" role="status">Отправляется…</p>}
            {message.delivery === "failed" && <div className="mt-2 text-xs text-danger"><p>{message.error || "Не удалось отправить."}</p><Button size="sm" variant="ghost" disabled={state !== "connected" || working} icon={<RotateCw size={12} />} onClick={() => void run(() => send(message.pendingBody!, { entities: message.entities, attachments: message.attachments, reply: message.reply }))}>Повторить отправку</Button></div>}
            {error && <p role="alert" className="mt-1 text-xs text-danger">{error}</p>}
        </div>
        {interactive && <div onPointerDownCapture={(event) => { if ((event.target as HTMLElement).closest("button")?.getAttribute("aria-label") === "Действия с сообщением") captureSelection(); }} onKeyDownCapture={(event) => { if (event.key === "Enter" || event.key === " ") captureSelection(); }} className="absolute top-4 right-2 flex items-center gap-0.5 rounded-lg text-muted sm:right-3">
            <div className="chat-message-reaction-trigger"><Popover label="Добавить реакцию" trigger={<SmilePlus size={14} />} width={280} triggerClassName="p-1.5 hover:text-primary" disabled={working || state !== "connected"}>
                {({ close }) => <div className="flex flex-wrap gap-1 p-1">{CHAT_REACTIONS.map((reaction) => <button key={reaction} type="button" aria-label={`Реакция ${reaction}`} className="chat-quick-reaction rounded p-2 text-lg hover:bg-hover" onClick={() => { close(); void run(() => command("reaction.set", { reaction, active: !message.reactions.some((item) => item.reaction === reaction && item.user_ids.includes(userId)) }, message.id)); }}>{reaction}</button>)}</div>}
            </Popover></div>
            <Popover label="Действия с сообщением" trigger={<MoreHorizontal size={15} />} width={280} triggerClassName="p-1.5 hover:text-primary">
                {({ close }) => <div className="flex flex-col p-1">
                    <div className="chat-mobile-reactions mb-1 hidden flex-wrap gap-1 border-b border-line pb-2" aria-label="Реакции">{CHAT_REACTIONS.map((reaction) => <button key={reaction} type="button" aria-label={`Реакция ${reaction}`} disabled={working || state !== "connected"} className="chat-quick-reaction flex min-h-11 min-w-11 items-center justify-center rounded text-lg hover:bg-hover" onClick={() => { close(); void run(() => command("reaction.set", { reaction, active: !message.reactions.some((item) => item.reaction === reaction && item.user_ids.includes(userId)) }, message.id)); }}>{reaction}</button>)}</div>
                    <Button variant="ghost" className="justify-start" icon={<CornerUpLeft size={13} />} onClick={() => { close(); onReply(message); }}>Ответить</Button>
                    {message.content && <Button variant="ghost" className="justify-start" icon={<Quote size={13} />} onClick={() => { quote(); close(); }}>Цитировать</Button>}
                    {own && <><Button variant="ghost" className="justify-start" icon={<Pencil size={13} />} onClick={() => { close(); onEdit(message); }}>Изменить</Button><Button variant="ghost" className="justify-start text-danger" icon={<Trash2 size={13} />} onClick={() => { close(); setDeleting(true); }}>Удалить</Button></>}
                </div>}
            </Popover>
        </div>}
        {deleting && <Modal title="Удалить сообщение?" description="На его месте останется отметка об удалении. Ответы других участников сохранятся." isOpen onOpenChange={setDeleting} footer={<><Button onClick={() => setDeleting(false)}>Отмена</Button><Button variant="destructive" disabled={working} onClick={() => void run(() => command("message.delete", { expected_revision: message.revision }, message.id))}>Удалить сообщение</Button></>}><div className="px-5 py-3 text-xs text-secondary">{message.content.slice(0, 240) || "Сообщение с вложением"}{error && <p className="mt-2 text-danger">{error}</p>}</div></Modal>}
    </article>;
}
