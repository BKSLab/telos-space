import { lazy, Suspense, useState } from "react";
import { Button } from "@/components/ui/Button";
import { ErrorMessage } from "@/components/ui/States";
import { copyText } from "@/lib/clipboard";
import type { GitFilePage } from "@/lib/projectKnowledge";
import { useRepositoryQuery } from "./repositoryApi";
import { sourcePageWindow } from "./sourcePage";

const MonacoSource = lazy(() => import("./MonacoSource"));
export interface SourceSelection { connection: number; sha: string; path: string; line: number; end: number; offset: number }

export function SourceViewer({ project, selection, onPage }: { project: number; selection: SourceSelection; onPage: (offset: number) => void }) {
    const [plain, setPlain] = useState(true);
    const [copied, setCopied] = useState("");
    const { connection, sha, path, line, end, offset } = selection;
    const file = useRepositoryQuery<GitFilePage>({ project, connection, sha, kind: "file", params: {
        path, max_chars: "8000", ...(offset ? { offset: String(offset) } : { line_start: String(line) }),
    } });
    const window = file.data ? sourcePageWindow(file.data) : null;
    return <section className="flex min-h-0 min-w-0 flex-1 flex-col gap-2" aria-label="Исходный файл">
        <div className="flex flex-wrap items-center gap-2 border-b border-line-subtle pb-2">
            <h3 className="min-w-0 flex-1 break-all font-mono text-[13px] text-primary">{path}</h3>
            <Button size="sm" aria-pressed={plain} onClick={() => setPlain(!plain)}>{plain ? "Подсветка кода" : "Обычный текст"}</Button>
            {file.data && !file.error && <Button size="sm" onClick={() => { void copyText(window!.text).then(() => setCopied("Скопировано"), () => setCopied("Выделите текст для копирования")); }}>Копировать фрагмент</Button>}
        </div>
        {file.isFetching && <p role="status" className="text-muted">Читаем выбранную версию…</p>}
        {file.error && <ErrorMessage message={file.error.message} action={<Button onClick={() => void file.refetch()}>Повторить чтение</Button>} />}
        {file.data && !file.error && <>
            <p className="break-all text-[12px] text-muted">SHA {file.data.commit_sha} · строки {file.data.line_start}–{file.data.line_start + (window!.text.match(/\n/g)?.length ?? 0) - (window!.text.endsWith("\n") ? 1 : 0)} · только чтение</p>
            {(file.data.partial || file.data.stale) && <p className="text-[12px] text-warning">Версия или охват неполны. Показан только прочитанный фрагмент.</p>}
            {plain ? <pre tabIndex={0} aria-label={`Код ${path}`} className="scrollbar-thin min-h-48 flex-1 overflow-auto rounded-md border border-line-subtle bg-surface-2 p-3 font-mono text-[12px] text-secondary">{window!.text.replace(/\n$/, "").split("\n").map((text, index) => {
                const number = file.data.line_start + index;
                return <span key={index} className={`block ${number >= line && number <= end ? "bg-accent/15" : ""}`}><span aria-hidden="true" className="mr-4 inline-block w-10 select-none text-right text-muted">{number}</span>{text}{"\n"}</span>;
            })}</pre> : <Suspense fallback={<p role="status">Загружаем редактор…</p>}><MonacoSource text={window!.text} path={path} firstLine={file.data.line_start} line={line} end={end} /></Suspense>}
            <div className="flex flex-wrap items-center gap-2">
                {window?.next != null && <Button onClick={() => onPage(window.next!)}>Читать дальше</Button>}
                {offset > 0 && <Button onClick={() => onPage(0)}>К выбранным строкам</Button>}
                {/^https:\/\/github\.com\//.test(file.data.source_url) && <a className="touch-target inline-flex items-center text-[13px] text-accent underline" href={file.data.source_url} target="_blank" rel="noopener noreferrer">Открыть в GitHub</a>}
                <span role="status" className="text-[12px] text-muted">{copied}</span>
            </div>
        </>}
    </section>;
}
