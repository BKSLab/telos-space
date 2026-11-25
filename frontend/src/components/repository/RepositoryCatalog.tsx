import { useState } from "react";
import { ChevronRight, FileText, Folder } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { ErrorMessage } from "@/components/ui/States";
import { GIT_METADATA_REASONS, type GitTreeEntry, type GitTreePage } from "@/lib/projectKnowledge";
import { useRepositoryQuery } from "./repositoryApi";

/** Shares the tree's immutable directory query through React Query. */
export function RepositoryCatalog({ project, connection, sha, directory, selectedPath, onDirectory, onSelect }: {
    project: number; connection: number; sha: string; directory: string; selectedPath: string;
    onDirectory: (path: string) => void; onSelect: (entry: GitTreeEntry) => void;
}) {
    const [offset, setOffset] = useState(0);
    const query = useRepositoryQuery<GitTreePage>({ project, connection, sha, kind: "tree", params: { children: "true", prefix: directory, offset: String(offset), limit: "40" } });
    return <section aria-label="Каталог файлов" className="repository-catalog">
        <nav aria-label="Путь в репозитории" className="repository-catalog-path">
            <Button size="sm" onClick={() => onDirectory("")}>Корень</Button>
            {directory.split("/").filter(Boolean).map((part, index, all) => <span className="inline-flex min-w-0 items-center gap-1" key={index}><ChevronRight size={12} aria-hidden="true" /><Button size="sm" onClick={() => onDirectory(all.slice(0, index + 1).join("/"))}>{part}</Button></span>)}
            {query.isFetching && <span role="status" className="ml-auto text-[11px] text-muted">Читаем…</span>}
        </nav>
        {query.error && <ErrorMessage message={query.error.message} />}
        {query.data && !query.error && <>
            {(query.data.truncated || query.data.stale) && <p className="px-3 text-[12px] text-warning">Каталог или версия неполны.</p>}
            <div className="repository-catalog-scroll"><table className="w-full text-left text-[12px]">
                <thead><tr><th>Имя</th><th>Содержимое</th><th className="text-right">Размер</th></tr></thead>
                <tbody>
                    {directory && <tr><td colSpan={3}><button className="repository-catalog-entry" onClick={() => onDirectory(directory.split("/").slice(0, -1).join("/"))}>↑ На уровень выше</button></td></tr>}
                    {query.data.items.map(entry => <tr key={entry.path} className={entry.path === selectedPath ? "repository-catalog-selected" : ""}>
                        <td><button className="repository-catalog-entry" title={entry.path} onClick={() => entry.type === "tree" ? onDirectory(entry.path) : onSelect(entry)}>{entry.type === "tree" ? <Folder size={15} className="shrink-0 text-accent" aria-hidden="true" /> : <FileText size={15} className="shrink-0 text-muted" aria-hidden="true" />}<span className="truncate">{entry.path.startsWith(`${directory}/`) ? entry.path.slice(directory.length + 1) : entry.path}</span></button></td>
                        <td className="text-muted">{entry.type === "tree" ? "Папка" : entry.index_state === "ready" ? "Текст" : entry.index_state === "error" ? "Ошибка чтения" : GIT_METADATA_REASONS[entry.index_reason ?? ""] ?? (entry.index_state === "pending" ? "Ожидает обработки" : "Метаданные")}</td>
                        <td className="whitespace-nowrap text-right text-muted">{entry.size == null ? "—" : entry.size < 1024 ? `${entry.size} Б` : `${(entry.size / 1024).toLocaleString("ru", { maximumFractionDigits: 1 })} КБ`}</td>
                    </tr>)}
                    {!query.data.items.length && <tr><td colSpan={3} className="text-muted">Каталог пуст в доступном индексе.</td></tr>}
                </tbody>
            </table></div>
            {(offset > 0 || query.data.next_offset !== null) && <nav aria-label="Страницы списка файлов" className="flex gap-2 border-t border-line-subtle p-2"><Button size="sm" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 40))}>Назад</Button><Button size="sm" disabled={query.data.next_offset === null} onClick={() => query.data?.next_offset != null && setOffset(query.data.next_offset)}>Дальше</Button></nav>}
        </>}
    </section>;
}
