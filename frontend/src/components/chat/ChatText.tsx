import { Fragment, type ReactNode } from "react";

/** Ограниченный markdown рендерится React-узлами: сырого HTML в чате нет. */
function inline(text: string): ReactNode[] {
    return text.split(/(\*\*[^*\n]+\*\*|`[^`\n]+`|https?:\/\/[^\s<>]+)/g).map((part, index) => {
        if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
        if (part.startsWith("`") && part.endsWith("`")) return <code key={index} className="rounded bg-white/5 px-1 font-mono text-[12px]">{part.slice(1, -1)}</code>;
        if (/^https?:\/\//.test(part)) return <a key={index} href={part} target="_blank" rel="noopener noreferrer" className="text-accent underline decoration-accent/35 underline-offset-2">{part}</a>;
        return <Fragment key={index}>{part}</Fragment>;
    });
}

export function ChatText({ content }: { content: string }) {
    return <div className="space-y-0.5 text-[14px] leading-relaxed break-words whitespace-pre-wrap [overflow-wrap:anywhere]">
        {content.split("\n").map((line, index) => line.startsWith("> ") ? <blockquote key={index} className="border-l-2 border-line pl-3 text-secondary">{inline(line.slice(2))}</blockquote> : <div key={index} className="min-h-[1lh]">{inline(line)}</div>)}
    </div>;
}
