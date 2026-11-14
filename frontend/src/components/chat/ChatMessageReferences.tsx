import { UserIdentity } from "@/components/users/UserIdentity";
import { Link } from "react-router-dom";
import { MessageSquareQuote } from "lucide-react";
import type { ChatMessageReference } from "@/lib/types";
import { chatMessageReferenceHref } from "@/lib/chatMessageReferences";

const states = {
    edited: "Сообщение изменено. Прежняя цитата больше не подтверждена.",
    deleted: "Сообщение удалено. Цитата недоступна.",
    unavailable: "Сообщение недоступно. Цитата скрыта.",
};

function Reference({ reference }: { reference: ChatMessageReference }) {
    const href = chatMessageReferenceHref(reference);
    const date = new Date(reference.created_at);
    const available = reference.state === "available";
    return <li className="min-w-0 rounded-md border border-line bg-surface/60 px-3 py-2 text-[12px] text-secondary">
        {reference.state !== "unavailable" && <div className="mb-1 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[11px] text-muted">
            <UserIdentity userId={reference.author_id} name={reference.author_name} className="font-medium text-secondary" />
            {!Number.isNaN(date.valueOf()) && <time dateTime={reference.created_at}>{date.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" })}</time>}
        </div>}
        {reference.state === "available"
            ? <blockquote className="whitespace-pre-wrap break-words border-l-2 border-accent/50 pl-2 leading-relaxed [overflow-wrap:anywhere]">{reference.quote}</blockquote>
            : <p role="status">{states[reference.state]}</p>}
        {href && <Link to={href} className="mt-1 inline-flex min-h-11 items-center gap-1.5 rounded px-1 text-accent hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
            <MessageSquareQuote size={14} aria-hidden="true" />{available ? "Открыть сообщение" : "Открыть текущее сообщение"}
        </Link>}
    </li>;
}

/** Старую цитату не показываем даже при ошибочном наличии quote у изменённого источника. */
export function ChatMessageReferences({ references = [] }: { references?: ChatMessageReference[] }) {
    if (references.length === 0) return null;
    return <section aria-label="Цитаты сообщений" className="min-w-0 space-y-1.5">
        <h3 className="text-[11px] font-medium text-muted">Цитаты сообщений</h3>
        <ul className="space-y-2">{references.map((reference, index) => <Reference key={`${reference.kind}:${reference.chat_id}:${reference.message_id}:${reference.start}:${index}`} reference={reference} />)}</ul>
    </section>;
}
