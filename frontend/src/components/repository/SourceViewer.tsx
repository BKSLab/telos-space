import { Component, lazy, Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import { Code2, Copy, ExternalLink, LockKeyhole } from "lucide-react";
import { Button, IconButton } from "@/components/ui/Button";
import { ErrorMessage } from "@/components/ui/States";
import { copyText } from "@/lib/clipboard";
import type { GitFilePage } from "@/lib/projectKnowledge";
import { useRepositoryQuery } from "./repositoryApi";
import { sourcePageWindow } from "./sourcePage";
import { StaticSource } from "./StaticSource";
import "./sourceViewer.css";

const MonacoSource = lazy(() => import("./MonacoSource"));
export interface SourceSelection { connection: number; sha: string; path: string; line: number; end: number; offset: number }
export interface SourceRange { line: number; end: number }

class HighlightBoundary extends Component<{ children: ReactNode; fallback: ReactNode }, { failed: boolean }> {
    state = { failed: false };
    static getDerivedStateFromError() { return { failed: true }; }
    render() { return this.state.failed ? <><p role="status" className="repository-source-notice">Подсветка недоступна. Показан обычный текст.</p>{this.props.fallback}</> : this.props.children; }
}

export function SourceViewer({ project, selection, onPage, onRange, showVersionNotice = true, showCoverageNotice = true }: {
    project: number; selection: SourceSelection; onPage: (offset: number) => void; onRange?: (range: SourceRange) => void; showVersionNotice?: boolean; showCoverageNotice?: boolean;
}) {
    const [plain, setPlain] = useState(false);
    const [copied, setCopied] = useState("");
    const root = useRef<HTMLElement>(null);
    const focusPage = useRef(false);
    const { connection, sha, path, line, end, offset } = selection;
    // Selecting a visible gutter range updates the permalink, not the loaded fragment.
    // External symbol/history navigation still moves the source reading anchor.
    const [anchor, setAnchor] = useState<{ line: number; observedLine: number; observedEnd: number; pending: SourceRange | null }>({ line, observedLine: line, observedEnd: end, pending: null });
    const changedRange = anchor.observedLine !== line || anchor.observedEnd !== end;
    const ownRange = anchor.pending?.line === line && anchor.pending?.end === end;
    const requestLine = changedRange && !ownRange ? line : anchor.line;
    if (changedRange) setAnchor({ line: requestLine, observedLine: line, observedEnd: end, pending: null });
    const selectRange = onRange ? (range: SourceRange) => {
        setAnchor(current => ({ ...current, pending: range }));
        onRange(range);
    } : undefined;
    const file = useRepositoryQuery<GitFilePage>({ project, connection, sha, kind: "file", params: {
        path, max_chars: "8000", ...(offset ? { offset: String(offset) } : { line_start: String(requestLine) }),
    } });
    useEffect(() => {
        if (focusPage.current && file.data && !file.isFetching) {
            root.current?.querySelector<HTMLElement>(".repository-source-static")?.focus();
            focusPage.current = false;
        }
    }, [file.data, file.isFetching]);
    const readPage = (pageOffset: number) => { focusPage.current = true; onPage(pageOffset); };
    const window = file.data ? sourcePageWindow(file.data) : null;
    const lastLine = file.data && window ? file.data.line_start + Math.max(0, window.text.replace(/\r?\n$/, "").split(/\r?\n/).length - 1) : null;
    const extension = path.split(".").pop()?.toLowerCase() ?? "";
    const language = ({ py: "Python", ts: "TypeScript", tsx: "TSX", js: "JavaScript", jsx: "JSX", md: "Markdown", json: "JSON", css: "CSS", html: "HTML", yml: "YAML", yaml: "YAML", sh: "Shell", sql: "SQL" } as Record<string, string>)[extension] ?? "Текст";
    const plainSource = file.data && window ? <StaticSource text={window.text} path={path} firstLine={file.data.line_start} line={line} end={end} onRange={selectRange} /> : null;
    return <section ref={root} className="repository-source-viewer" aria-label="Исходный файл">
        <header className="repository-source-toolbar">
            <Code2 size={16} aria-hidden="true" className="text-accent shrink-0" />
            <h3 className="repository-source-filename" title={path}>{path.split("/").pop()}</h3>
            <span className="repository-source-language">{language}</span>
            <div className="repository-source-actions">
                <Button size="sm" variant="ghost" aria-pressed={plain} onClick={() => setPlain(!plain)}>{plain ? "Подсветка кода" : "Обычный текст"}</Button>
                {file.data && !file.error && <>
                    <IconButton size="sm" label="Копировать фрагмент" onClick={() => { void copyText(window!.text).then(() => setCopied("Скопировано"), () => setCopied("Выделите текст для копирования")); }}><Copy size={15} aria-hidden="true" /></IconButton>
                    {/^https:\/\/github\.com\//.test(file.data.source_url) && <a className="repository-source-external touch-target" aria-label="Открыть в GitHub" title="Открыть в GitHub" href={file.data.source_url} target="_blank" rel="noopener noreferrer"><ExternalLink size={15} aria-hidden="true" /></a>}
                </>}
            </div>
        </header>
        <div className="repository-source-version">
            <span className="repository-source-path" title={path}>{path}</span>
            <span title={sha} className="font-mono">{sha.slice(0, 7)}</span>
            {lastLine !== null && <span>строки {file.data!.line_start}–{lastLine}</span>}
            <span className="repository-source-readonly"><LockKeyhole size={11} aria-hidden="true" />Только чтение</span>
        </div>
        {file.isFetching && <p role="status" className="repository-source-notice">Читаем выбранную версию…</p>}
        {file.error && <ErrorMessage message={file.error.message} action={<Button onClick={() => void file.refetch()}>Повторить чтение</Button>} />}
        {file.data && !file.error && <>
            {showCoverageNotice && file.data.partial && <p className="repository-source-notice text-warning">Индекс этой версии охватывает не весь репозиторий. Доступный текст файла показан ниже.</p>}
            {showVersionNotice && file.data.stale && <p className="repository-source-notice">Открыта сохранённая версия файла {sha.slice(0, 7)}; ветка уже обновилась.</p>}
            {plain ? plainSource : <HighlightBoundary key={`${connection}:${sha}:${path}`} fallback={plainSource}><Suspense fallback={<><p role="status" className="repository-source-notice">Загружаем подсветку…</p>{plainSource}</>}><MonacoSource text={window!.text} path={path} firstLine={file.data.line_start} line={line} end={end} onRange={selectRange} /></Suspense></HighlightBoundary>}
            <footer className="repository-source-footer">
                {window?.next != null && <><span className="text-[12px] text-muted">Файл загружается частями</span><Button size="sm" onClick={() => readPage(window.next!)}>Читать дальше</Button></>}
                {offset > 0 && <Button size="sm" variant="ghost" onClick={() => { setAnchor(current => ({ ...current, line })); readPage(0); }}>К выбранным строкам</Button>}
                <span role="status" className="text-[12px] text-muted">{copied}</span>
            </footer>
        </>}
    </section>;
}
