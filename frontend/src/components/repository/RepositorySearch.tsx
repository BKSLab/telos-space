import { useContext, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { ErrorMessage } from "@/components/ui/States";
import { api, ApiError } from "@/lib/api";
import { contextApi, type GitSourceHit } from "@/lib/projectKnowledge";
import { RepositoryAccess, repositoryKey } from "./repositoryApi";

export function RepositorySearch({ project, connection, sha, value, onQuery, onOpen }: { project: number; connection: number; sha: string; value: string; onQuery: (query: string) => void; onOpen: (hit: GitSourceHit, map?: boolean) => void }) {
    const [input, setInput] = useState({ url: value, text: value });
    // Back/Forward supplies a new committed URL. Keep the same input DOM node and focus.
    if (input.url !== value) setInput({ url: value, text: value });
    const draft = input.url === value ? input.text : value;
    const revoke = useContext(RepositoryAccess);
    useEffect(() => { if (draft === value) return; const timeout = setTimeout(() => onQuery(draft.trim()), 350); return () => clearTimeout(timeout); }, [draft, value, onQuery]);
    const [language, setLanguage] = useState("");
    const [symbols, setSymbols] = useState(false);
    const search = useQuery({ queryKey: [...repositoryKey(project, connection || "all"), sha, "search", value], enabled: Boolean(value), retry: false,
        queryFn: async ({ signal }) => {
            const params = new URLSearchParams({ q: value, limit: "50" });
            if (connection) params.set("connection_id", String(connection));
            if (connection && sha) params.set("sha", sha);
            try { return await api.get<{ items: GitSourceHit[]; partial: boolean }>(`${contextApi.git(project)}/search?${params}`, { signal }); }
            catch (error) { if (error instanceof ApiError && [401, 403].includes(error.status)) revoke(connection); throw error; }
        },
    });
    const items = search.data?.items.filter(hit => (!language || hit.path.endsWith(language)) && (!symbols || hit.symbol_id != null)) ?? [];
    return <section className="min-w-0 space-y-3" aria-label="Поиск по коду">
        <form onSubmit={event => { event.preventDefault(); onQuery(draft.trim()); }} className="flex flex-wrap items-end gap-2">
            <label className="min-w-0 flex-1 text-[12px] text-muted">Найти код и документацию<Input type="search" aria-label="Найти код и документацию" value={draft} maxLength={500} onChange={event => setInput({ url: value, text: event.target.value })} /></label>
            <Button type="submit" disabled={!draft.trim()}>Найти в репозиториях</Button>
        </form>
        <div className="flex flex-wrap gap-3 text-[12px] text-muted">
            <label>Язык <select aria-label="Язык результатов" className="control-surface rounded p-1" value={language} onChange={event => setLanguage(event.target.value)}><option value="">Все</option><option value=".py">Python</option><option value=".ts">TypeScript</option><option value=".js">JavaScript</option><option value=".md">Markdown</option></select></label>
            <label className="touch-target flex items-center gap-2"><input type="checkbox" checked={symbols} onChange={event => setSymbols(event.target.checked)} />Только символы</label>
        </div>
        {(language || symbols) && <p className="text-[12px] text-muted">Фильтры применены к полученной ограниченной странице. Состав индекса не меняется.</p>}
        {search.isFetching && <p role="status" className="text-muted">Ищем доступные фрагменты…</p>}
        {search.error && <ErrorMessage message={search.error.message} />}
        {search.data && !search.error && <>
            {search.data.partial && <p className="text-[12px] text-warning">Поиск неполный: часть источников не готова или недоступна.</p>}
            {items.length === 0 && <p className="text-[13px] text-muted">В доступной части индекса совпадений нет.</p>}
            <ul className="divide-y divide-line-subtle">{items.map((hit, index) => <li key={`${hit.connection_id}:${hit.file_id}:${hit.symbol_id ?? index}`} className="space-y-2 py-3">
                <p className="break-all text-[13px] text-primary">Репозиторий {hit.repository_id} · {hit.path}{hit.qualified_name ? ` · ${hit.qualified_name}` : ""}</p>
                <p className="break-all font-mono text-[11px] text-muted">{hit.commit_sha}{hit.line_start ? ` · строки ${hit.line_start}–${hit.line_end ?? hit.line_start}` : ""} · {hit.symbol_id ? "Совпадение символа" : "Совпадение в тексте или пути"}</p>
                <pre className="overflow-x-auto whitespace-pre-wrap break-words text-[12px] text-secondary">{hit.excerpt}</pre>
                <div className="flex gap-2"><Button size="sm" onClick={() => onOpen(hit)}>Открыть</Button><Button size="sm" onClick={() => onOpen(hit, true)}>На карте</Button></div>
            </li>)}</ul>
            {search.data.items.length >= 50 && <p className="text-[12px] text-warning">Показано до 50 совпадений. Уточните запрос или выберите репозиторий.</p>}
        </>}
    </section>;
}
