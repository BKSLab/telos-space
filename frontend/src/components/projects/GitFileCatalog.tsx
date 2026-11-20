import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, FileText, Files } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Field";
import { ErrorMessage } from "@/components/ui/States";
import { api, queryKeys } from "@/lib/api";
import { cn } from "@/lib/cn";
import { contextApi, GIT_METADATA_REASONS, type GitConnection, type GitSourceHit, type GitTreeEntry, type GitTreePage } from "@/lib/projectKnowledge";

const states = { pending: "Ожидает обработки", ready: "Текст готов", metadata: "Только метаданные", error: "Ошибка чтения" };
function reason(entry: GitTreeEntry) {
    if (entry.index_state === "metadata") return GIT_METADATA_REASONS[entry.index_reason ?? ""] ?? "Содержимое не представлено текстом";
    if (entry.index_state === "error") return entry.index_reason === "transport_limit" ? "Не удалось загрузить содержимое: ограничение передачи данных" : "Не удалось прочитать содержимое";
    if (entry.mode === "120000") return "Текст символической ссылки; переход по ней не выполняется";
    return null;
}

export function GitFileCatalog({ connection, onRead }: { connection: GitConnection; onRead: (hit: Pick<GitSourceHit, "connection_id" | "commit_sha" | "path">) => void }) {
    const [open, setOpen] = useState(false);
    const [path, setPath] = useState("");
    const [prefix, setPrefix] = useState("");
    const [offset, setOffset] = useState(0);
    const query = useQuery({
        queryKey: [...queryKeys.project(connection.project_id), "git", "tree", connection.id, connection.observed_sha, prefix, offset],
        queryFn: () => {
            const params = new URLSearchParams({ offset: String(offset), limit: "30" });
            if (prefix) params.set("prefix", prefix);
            if (connection.observed_sha) params.set("sha", connection.observed_sha);
            return api.get<GitTreePage>(`${contextApi.git(connection.project_id)}/connections/${connection.id}/tree?${params}`);
        },
        enabled: open && connection.enabled && connection.state !== "access_lost",
        refetchOnWindowFocus: false,
    });
    if (!connection.enabled || connection.state === "access_lost") return null;
    return <details className="group/catalog border-t border-line-subtle pt-1" onToggle={event => setOpen(event.currentTarget.open)}>
        <summary className="touch-target flex cursor-pointer list-none items-center gap-2 text-[13px] font-medium text-secondary focus-visible:outline-2 focus-visible:outline-accent [&::-webkit-details-marker]:hidden"><Files size={15} aria-hidden="true" />Каталог файлов<ChevronRight size={14} aria-hidden="true" className="ml-auto shrink-0 group-open/catalog:rotate-90" /></summary>
        <div className="space-y-3 py-2">
            <p className="text-[12px] text-muted">Все файлы выбранной версии, включая настройки, двоичные файлы и подмодули. Поиск ниже меняет только отображение каталога, а не состав индекса.</p>
            <form className="flex flex-wrap items-end gap-2" onSubmit={event => { event.preventDefault(); setPrefix(path.trim().replace(/\/$/, "")); setOffset(0); }}>
                <Field label="Файл или папка" hint="Точный путь от корня репозитория, например docs или .env.example." className="min-w-0 flex-1">{id => <Input id={id} value={path} maxLength={4096} onChange={event => setPath(event.target.value)} />}</Field>
                <Button size="sm" type="submit">Показать</Button>
                {prefix && <Button size="sm" onClick={() => { setPath(""); setPrefix(""); setOffset(0); }}>Весь каталог</Button>}
            </form>
            <div className="flex flex-wrap items-center justify-between gap-2 text-[12px] text-muted"><span>Поиск по содержимому доступен для текстовых файлов.</span><Button size="sm" disabled={query.isFetching} onClick={() => void query.refetch()}>Обновить каталог</Button></div>
            {query.isFetching && <p role="status" className="text-[12px] text-muted">Читаем каталог…</p>}
            {query.error && <ErrorMessage message={query.error.message} />}
            {query.data && !query.error && !query.isFetching && <>
                <p className="break-all text-[12px] text-muted">Версия <code title={query.data.commit_sha}>{query.data.commit_sha.slice(0, 8)}</code> · {prefix ? "В выбранном пути" : "Всего файлов"}: {query.data.total_files}</p>
                {(query.data.truncated || query.data.stale) && <p className="text-[12px] text-warning">Каталог или версия пока неполны. Проверьте состояние обновления репозитория.</p>}
                {query.data.items.length === 0 && <p className="text-[12px] text-muted">Файлы по этому пути не найдены в доступном каталоге.</p>}
                <ul className="divide-y divide-line-subtle rounded-md border border-line-subtle px-3" aria-label="Файлы репозитория">
                    {query.data.items.map(entry => <li key={entry.path} className="min-w-0 space-y-1 py-3 text-[12px]">
                        <div className="flex flex-wrap items-start justify-between gap-2"><span className="min-w-0 break-all font-mono text-secondary">{entry.path}</span><span className={cn("shrink-0", entry.index_state === "error" ? "text-warning" : entry.index_state === "ready" ? "text-success" : "text-muted")}>{states[entry.index_state]}</span></div>
                        <p className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted"><code title={entry.sha}>{entry.sha.slice(0, 8)}</code>{entry.size !== null && <span>{entry.size.toLocaleString("ru-RU")} Б</span>}{reason(entry) && <span>{reason(entry)}</span>}</p>
                        {entry.index_state === "ready" && <Button size="sm" aria-label={`Читать ${entry.path}`} icon={<FileText size={13} aria-hidden="true" />} onClick={() => onRead({ connection_id: connection.id, commit_sha: query.data!.commit_sha, path: entry.path })}>Читать текст</Button>}
                    </li>)}
                </ul>
                {(offset > 0 || query.data.next_offset !== null) && <nav className="flex flex-wrap items-center gap-2" aria-label="Страницы каталога файлов"><Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 30))}>Назад</Button><span className="text-[12px] text-muted">{offset + 1}–{offset + query.data.items.length} из {query.data.total_files}</span><Button size="sm" disabled={query.data.next_offset === null} onClick={() => query.data?.next_offset !== null && query.data && setOffset(query.data.next_offset)}>Дальше</Button></nav>}
            </>}
        </div>
    </details>;
}
