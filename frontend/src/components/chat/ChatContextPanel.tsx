import { FileText, ListTodo, PanelRight, Pin, PinOff, X } from "lucide-react";
import { CHAT_CONTEXT_LABELS, chatContextKey, useChatContext } from "@/lib/chatContext";
import { Button, IconButton } from "@/components/ui/Button";
import { Drawer } from "@/components/ui/Drawer";
import { ChatContextDetails } from "./ChatContextDetails";

const ICONS = { task: ListTodo, document: FileText };

export function ChatContextToggle({ obscured, onShow }: { obscured: boolean; onShow: () => void }) {
    const context = useChatContext();
    if (!context) return null;
    const visible = context.isOpen && !obscured;
    return <Button id="project-chat-context-toggle" variant="ghost" aria-label={visible ? "Скрыть контекст" : "Показать контекст"}
        aria-expanded={visible} aria-controls="project-chat-context" icon={<PanelRight size={16} aria-hidden="true" />}
        onClick={() => { if (visible) context.close(); else { onShow(); context.show(); } }}>
        <span className="hidden sm:inline">Контекст</span>
    </Button>;
}

export function ChatContextPanel({ hidden }: { hidden: boolean }) {
    const context = useChatContext();
    if (!context || !context.isOpen || hidden) return null;
    const header = <div className="flex min-w-0 flex-1 items-center gap-2">
        <PanelRight size={15} className="shrink-0 text-ai-cyan" aria-hidden="true" />
        <h2 className="min-w-0 flex-1 text-sm font-semibold text-primary">Контекст</h2>
        {context.target && <IconButton label={context.pinned ? "Открепить контекст" : "Закрепить контекст"} aria-pressed={context.pinned} onClick={context.togglePin}>
            {context.pinned ? <PinOff size={15} aria-hidden="true" /> : <Pin size={15} aria-hidden="true" />}
        </IconButton>}
    </div>;
    const body = <div id="project-chat-context" className="chat-content min-w-0 space-y-5 p-4 [overflow-wrap:anywhere]">
        {context.pinned && <p role="status" className="text-[12px] text-muted">Закреплено — новые ответы не меняют выбор.</p>}
        {context.target ? <ChatContextDetails key={`${chatContextKey(context.target)}:${context.selectionKey}`} target={context.target} projectId={context.projectId} projectKey={context.projectKey}
            onOpenFull={() => { if (!context.desktop) context.close(); }} /> : <div className="py-3">
            <p className="text-sm font-medium text-primary">Задача или источник рядом с чатом</p>
            <p className="mt-2 text-[13px] leading-relaxed text-secondary">Выберите карточку в переписке, чтобы прочитать детали и продолжить обсуждение.</p>
            <p className="mt-2 text-[12px] leading-relaxed text-muted">Основной документ или единственный источник ответа Ариса появится здесь автоматически. Нужный объект можно закрепить.</p>
        </div>}
        {context.recent.length > 0 && <section aria-label="Объекты из переписки" className="border-t border-line-subtle pt-4">
            <h3 className="mb-2 text-[11px] font-semibold tracking-wide text-muted uppercase">Объекты из переписки</h3>
            <div className="space-y-1">{context.recent.map((item) => {
                const Icon = ICONS[item.type];
                const selected = context.target && chatContextKey(context.target) === chatContextKey(item);
                return <button key={chatContextKey(item)} type="button" aria-pressed={Boolean(selected)} onClick={() => context.select(item)}
                    className="flex w-full min-w-0 items-start gap-2.5 rounded-lg px-2 py-2.5 text-left text-secondary transition-colors hover:bg-hover aria-pressed:bg-accent/10 aria-pressed:text-accent focus-visible:outline-2 focus-visible:outline-accent">
                    <Icon size={14} className="mt-1 shrink-0" aria-hidden="true" /><span className="min-w-0"><span className="block text-[10px] text-muted">{CHAT_CONTEXT_LABELS[item.type]}</span><span className="mt-0.5 line-clamp-2 block text-[13px]">{item.title}</span></span>
                </button>;
            })}</div>
        </section>}
    </div>;
    if (!context.desktop) return <Drawer isOpen label="Контекст чата" header={<div className="brand-ai-header rounded-md px-1 py-2">{header}</div>} onClose={context.close} closeLabel="Закрыть контекст">{body}</Drawer>;
    return <aside aria-label="Контекст чата" className="chat-context-panel brand-ai-panel flex min-h-0 min-w-0 shrink-0 flex-col overflow-hidden rounded-[var(--radius-panel)] border border-transparent shadow-card">
        <header className="brand-ai-header material-metal flex shrink-0 items-center gap-2 border-b border-line-subtle px-4 py-3">{header}<IconButton label="Закрыть контекст" onClick={context.close}><X size={15} aria-hidden="true" /></IconButton></header>
        <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto overscroll-contain">{body}</div>
    </aside>;
}
