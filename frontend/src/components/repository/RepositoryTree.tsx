import { useState, type KeyboardEvent } from "react";
import { ChevronRight, FileText, Folder } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { ErrorMessage } from "@/components/ui/States";
import { GIT_METADATA_REASONS, type GitTreeEntry, type GitTreePage } from "@/lib/projectKnowledge";
import { useRepositoryQuery } from "./repositoryApi";

export function RepositoryTree({ project, connection, sha, directory, selectedPath, onDirectory, onSelect }: {
    project: number; connection: number; sha: string; directory: string; selectedPath: string;
    onDirectory: (path: string) => void; onSelect: (entry: GitTreeEntry) => void;
}) {
    const [offset, setOffset] = useState(0);
    const [draft, setDraft] = useState(directory);
    const query = useRepositoryQuery<GitTreePage>({ project, connection, sha, kind: "tree", params: { children: "true", prefix: directory, offset: String(offset), limit: "40" } });
    function navigate(path: string) { setOffset(0); setDraft(path); onDirectory(path); }
    function keyboard(event: KeyboardEvent<HTMLElement>) {
        const items = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('[role="treeitem"]'));
        const index = items.indexOf(document.activeElement as HTMLElement);
        if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
            event.preventDefault();
            items[event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : Math.max(0, Math.min(items.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))]?.focus();
        }
        if (event.key === "ArrowLeft" && directory) { event.preventDefault(); navigate(directory.split("/").slice(0, -1).join("/")); }
        if (event.key === "ArrowRight") { event.preventDefault(); (document.activeElement as HTMLElement)?.click(); }
    }
    return <div className="flex min-h-0 min-w-0 flex-col gap-3">
        <form className="flex gap-1" onSubmit={event => { event.preventDefault(); navigate(draft.trim().replace(/\/$/, "")); }}>
            <Input aria-label="Файл или папка" className="min-w-0" value={draft} onChange={event => setDraft(event.target.value)} placeholder="Путь от корня" />
            <Button type="submit" size="sm">Показать</Button>
        </form>
        <nav aria-label="Путь в репозитории" className="flex flex-wrap items-center gap-1 text-[12px]">
            <Button size="sm" onClick={() => navigate("")}>Корень</Button>
            {directory.split("/").filter(Boolean).map((part, index, all) => <Button key={index} size="sm" onClick={() => navigate(all.slice(0, index + 1).join("/"))}>{part}</Button>)}
        </nav>
        {query.isFetching && <p role="status" className="text-[12px] text-muted">Читаем каталог…</p>}
        {query.error && <ErrorMessage message={query.error.message} action={<Button onClick={() => void query.refetch()}>Повторить каталог</Button>} />}
        {query.data && !query.error && <>
            <p className="text-[12px] text-muted">Файлов в каталоге: {query.data.total_files}. Текст и структура обрабатываются отдельно.</p>
            {(query.data.truncated || query.data.stale) && <p className="text-[12px] text-warning">Каталог или версия неполны.</p>}
            {query.data.items.length === 0 && <p className="text-[12px] text-muted">В доступном каталоге нет элементов.</p>}
            <div role="tree" aria-label="Файлы репозитория" onKeyDown={keyboard} className="scrollbar-thin min-h-0 space-y-1 overflow-y-auto">
                {query.data.items.map(entry => <button key={entry.path} type="button" role="treeitem" aria-selected={entry.path === selectedPath}
                    aria-expanded={entry.type === "tree" ? false : undefined} aria-level={directory.split("/").filter(Boolean).length + 1}
                    className="touch-target flex w-full min-w-0 items-start gap-2 rounded-md px-2 py-2 text-left text-[12px] text-secondary hover:bg-hover aria-selected:bg-accent/15 focus-visible:outline-2 focus-visible:outline-accent"
                    onClick={() => entry.type === "tree" ? navigate(entry.path) : onSelect(entry)}>
                    {entry.type === "tree" ? <Folder size={15} className="mt-0.5 shrink-0 text-accent" aria-hidden="true" /> : <FileText size={15} className="mt-0.5 shrink-0" aria-hidden="true" />}
                    <span className="min-w-0 flex-1 break-all">{entry.path.startsWith(`${directory}/`) ? entry.path.slice(directory.length + 1) : entry.path}
                        {entry.type !== "tree" && <span className="block text-[11px] text-muted">{entry.index_state === "metadata" ? GIT_METADATA_REASONS[entry.index_reason ?? ""] ?? entry.index_reason : entry.index_state === "error" ? "Ошибка чтения" : entry.index_state === "pending" ? "Ожидает обработки" : "Текст готов"}{entry.size !== null ? ` · ${entry.size} Б` : ""}</span>}
                    </span>{entry.type === "tree" && <ChevronRight size={14} aria-hidden="true" />}
                </button>)}
            </div>
            {(offset > 0 || query.data.next_offset !== null) && <nav aria-label="Страницы каталога файлов" className="flex flex-wrap gap-2">
                <Button size="sm" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 40))}>Назад</Button>
                <Button size="sm" disabled={query.data.next_offset === null} onClick={() => query.data?.next_offset != null && setOffset(query.data.next_offset)}>Дальше</Button>
            </nav>}
        </>}
    </div>;
}
