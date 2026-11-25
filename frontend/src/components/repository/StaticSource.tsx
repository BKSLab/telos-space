import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import type { SourceRange } from "./SourceViewer";

export interface SourceToken { offset: number; type: string }
export interface StaticSourceProps {
    text: string; path: string; firstLine: number; line: number; end: number;
    onRange?: (range: SourceRange) => void;
    tokens?: SourceToken[][];
}

// Preserve original line endings and every character, including a final newline.
// Gutter controls stay outside the code for native copying and reading order.
function sourceLines(text: string) {
    // File API line numbers count LF; a standalone CR is source text.
    return text.match(/[^\n]*(?:\n|$)/g)?.filter((part, index, all) => part !== "" || (index === 0 && all.length === 1)) ?? [""];
}

function tokenClass(type: string) {
    const kind = type.split(".")[0];
    return ["comment", "keyword", "string", "number", "type", "tag", "attribute", "regexp"].includes(kind) ? `repository-source-token-${kind}` : undefined;
}

export function StaticSource({ text, path, firstLine, line, end, onRange, tokens }: StaticSourceProps) {
    const root = useRef<HTMLDivElement>(null);
    const buttons = useRef(new Map<number, HTMLButtonElement>());
    const lines = sourceLines(text);
    const lastLine = firstLine + lines.length - 1;
    const [focused, setFocused] = useState(line);
    const focusLine = Math.max(firstLine, Math.min(lastLine, focused));
    const gesture = useRef({ anchor: line, path, firstLine, pending: [] as SourceRange[] });

    useLayoutEffect(() => {
        const current = gesture.current;
        const ownUpdate = current.pending.findIndex(range => range.line === line && range.end === end);
        if (current.path !== path || current.firstLine !== firstLine || ownUpdate < 0) {
            gesture.current = { anchor: line, path, firstLine, pending: [] };
        } else {
            // A router may apply several selections in a single transition.
            current.pending.splice(0, ownUpdate + 1);
        }
    }, [line, end, path, firstLine]);

    useEffect(() => {
        const viewport = root.current;
        if (!viewport || line < firstLine || line > lastLine) return;
        const top = (line - firstLine) * 21;
        if (top < viewport.scrollTop || top + 21 > viewport.scrollTop + viewport.clientHeight) viewport.scrollTop = Math.max(0, top - 42);
    }, [text, firstLine, lastLine, line]);

    const select = (number: number, extend: boolean) => {
        const current = gesture.current;
        if (!extend) current.anchor = number;
        const range = { line: Math.min(current.anchor, number), end: Math.max(current.anchor, number) };
        if (range.line === line && range.end === end) {
            current.pending = [];
        } else {
            current.pending = current.pending.filter(previous => previous.line !== range.line || previous.end !== range.end);
            current.pending.push(range);
        }
        onRange?.(range);
    };
    function move(event: KeyboardEvent<HTMLButtonElement>, number: number) {
        const target = ({ ArrowUp: number - 1, ArrowDown: number + 1, Home: firstLine, End: lastLine } as Record<string, number>)[event.key];
        if (target === undefined) return;
        event.preventDefault();
        const next = Math.max(firstLine, Math.min(lastLine, target));
        setFocused(next);
        buttons.current.get(next)?.focus();
        if (event.shiftKey) select(next, true);
    }

    return <div ref={root} role="region" tabIndex={0} aria-label={`Код ${path}`} className={`repository-source-static scrollbar-thin ${tokens ? "repository-source-editor" : "repository-source-plain"}`}>
        <div className="repository-source-content">
            <pre className="repository-source-pre"><code className="repository-source-code">{lines.map((content, index) => {
                const number = firstLine + index;
                const ending = content.match(/\r?\n$/)?.[0] ?? "";
                const body = ending ? content.slice(0, -ending.length) : content;
                const parts = tokens?.[index];
                return <span key={index} className={`repository-source-line${number >= line && number <= end ? " repository-source-range" : ""}`} data-line={number}>{parts?.length ? <>{body.slice(0, parts[0].offset)}{parts.map((token, tokenIndex) => <span key={tokenIndex} className={tokenClass(token.type)}>{body.slice(token.offset, parts[tokenIndex + 1]?.offset ?? body.length)}</span>)}</> : body}{ending}</span>;
            })}</code></pre>
            <div className="repository-source-gutter" role={onRange ? "group" : undefined} aria-label={onRange ? "Выбор строк. Стрелки перемещают фокус; Shift со стрелками или нажатием выбирает диапазон." : undefined} aria-hidden={onRange ? undefined : true}>
                {lines.map((_, index) => {
                    const number = firstLine + index;
                    const selected = number >= line && number <= end;
                    return onRange ? <button key={number} ref={node => { if (node) buttons.current.set(number, node); else buttons.current.delete(number); }} type="button" className={`repository-source-line-number${selected ? " repository-source-range" : ""}`} tabIndex={number === focusLine ? 0 : -1} aria-label={`Выбрать строку ${number}`} aria-pressed={selected} onFocus={() => setFocused(number)} onKeyDown={event => move(event, number)} onClick={event => select(number, event.shiftKey)}>{number}</button> : <span key={number} className="repository-source-line-number">{number}</span>;
                })}
            </div>
        </div>
    </div>;
}
