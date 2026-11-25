import { useState, type KeyboardEvent } from "react";
import { ChevronDown, ChevronRight, FileText, Folder, FolderOpen } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { ErrorMessage } from "@/components/ui/States";
import { type GitTreeEntry, type GitTreePage } from "@/lib/projectKnowledge";
import { useRepositoryQuery } from "./repositoryApi";

interface TreeProps {
    project: number; connection: number; sha: string; directory: string; selectedPath: string;
    onDirectory: (path: string) => void; onSelect: (entry: GitTreeEntry) => void;
}

export function RepositoryTree(props: TreeProps) {
    const [draft, setDraft] = useState("");
    const [expanded, setExpanded] = useState<Record<string, boolean>>(() => {
        const ancestors = props.selectedPath.split("/").slice(0, -1);
        return Object.fromEntries(ancestors.map((_, index) => [ancestors.slice(0, index + 1).join("/"), true]));
    });
    const activeDirectory = props.directory;
    function toggle(path: string, open: boolean) {
        setExpanded(current => ({ ...current, [path]: open }));
        props.onDirectory(open ? path : path.split("/").slice(0, -1).join("/"));
    }
    function keyboard(event: KeyboardEvent<HTMLElement>) {
        const items = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('[role="treeitem"]'));
        const item = document.activeElement as HTMLElement;
        const index = items.indexOf(item);
        if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
            event.preventDefault();
            items[event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : Math.max(0, Math.min(items.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))]?.focus();
        } else if (event.key === "ArrowRight" && item.getAttribute("aria-expanded") === "false") {
            event.preventDefault(); item.click();
        } else if (event.key === "ArrowRight" && item.getAttribute("aria-expanded") === "true") {
            event.preventDefault(); items[index + 1]?.focus();
        } else if (event.key === "ArrowLeft") {
            event.preventDefault();
            if (item.getAttribute("aria-expanded") === "true") item.click();
            else items.find(candidate => candidate.dataset.path === item.dataset.parent)?.focus();
        }
    }
    return <div className="flex min-h-0 min-w-0 flex-col gap-3">
        <div className="flex items-center justify-between gap-2"><h3 className="text-[13px] font-semibold text-primary">Файлы</h3><Button size="sm" onClick={() => { setExpanded({}); props.onDirectory(""); }}>Корень</Button></div>
        <form className="flex gap-1" onSubmit={event => { event.preventDefault(); const path = draft.trim().replace(/^\/+|\/+$/g, ""); setExpanded({}); props.onDirectory(path); }}>
            <Input aria-label="Файл или папка" className="min-w-0" value={draft} onChange={event => setDraft(event.target.value)} placeholder="Перейти к папке…" />
            <Button type="submit" size="sm">Перейти</Button>
        </form>
        <div role="tree" aria-label="Файлы репозитория" onKeyDown={keyboard} className="repository-file-tree">
            <TreeBranch {...props} prefix="" level={1} expanded={expanded} activeDirectory={activeDirectory} onToggle={toggle} />
        </div>
    </div>;
}

function TreeBranch({ prefix, level, expanded, activeDirectory, onToggle, ...props }: TreeProps & {
    prefix: string; level: number; expanded: Record<string, boolean>; activeDirectory: string; onToggle: (path: string, open: boolean) => void;
}) {
    const [offset, setOffset] = useState(0);
    const query = useRepositoryQuery<GitTreePage>({ project: props.project, connection: props.connection, sha: props.sha, kind: "tree", params: { children: "true", prefix, offset: String(offset), limit: "40" } });
    return <>
        {query.isFetching && <p role="status" className="px-2 text-[12px] text-muted">Читаем каталог…</p>}
        {query.error && <ErrorMessage message={query.error.message} action={<Button onClick={() => void query.refetch()}>Повторить каталог</Button>} />}
        {query.data && !query.error && <>
            {(query.data.truncated || query.data.stale) && <p className="px-2 text-[12px] text-warning">Каталог или версия неполны.</p>}
            {query.data.items.length === 0 && <p className="px-2 text-[12px] text-muted">В каталоге нет элементов.</p>}
            {query.data.items.map(entry => {
                const folder = entry.type === "tree";
                const open = activeDirectory === entry.path || activeDirectory.startsWith(`${entry.path}/`) || expanded[entry.path] === true;
                const Icon = folder ? open ? FolderOpen : Folder : FileText;
                return <div key={entry.path}>
                    <button type="button" role="treeitem" aria-label={entry.path.slice(prefix ? prefix.length + 1 : 0)} aria-selected={folder ? entry.path === props.directory : entry.path === props.selectedPath}
                        aria-expanded={folder ? open : undefined} aria-level={level} data-path={entry.path} data-parent={prefix}
                        className="repository-tree-entry" style={{ paddingLeft: `${8 + (level - 1) * 16}px` }} title={entry.path}
                        onClick={() => folder ? onToggle(entry.path, !open) : props.onSelect(entry)}>
                        {folder ? open ? <ChevronDown size={13} aria-hidden="true" /> : <ChevronRight size={13} aria-hidden="true" /> : <span className="w-[13px] shrink-0" />}
                        <Icon size={16} className={folder ? "text-accent" : "text-muted"} aria-hidden="true" />
                        <span className="min-w-0 truncate">{entry.path.slice(prefix ? prefix.length + 1 : 0)}</span>
                        {entry.index_state === "error" && <span className="ml-auto text-warning" title="Ошибка чтения">!</span>}
                    </button>
                    {folder && open && <div role="group"><TreeBranch {...props} prefix={entry.path} level={level + 1} expanded={expanded} activeDirectory={activeDirectory} onToggle={onToggle} /></div>}
                </div>;
            })}
            {(offset > 0 || query.data.next_offset !== null) && <nav aria-label={`Страницы каталога файлов ${prefix || "корень"}`} className="flex flex-wrap gap-1 py-2">
                <Button size="sm" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 40))}>Назад</Button>
                <Button size="sm" disabled={query.data.next_offset === null} onClick={() => query.data?.next_offset != null && setOffset(query.data.next_offset)}>Дальше</Button>
            </nav>}
        </>}
    </>;
}
