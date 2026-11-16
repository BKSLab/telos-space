import type { AssertionReference, ExternalSourceReference } from "@/lib/types";

function publicLink(url: string | null): string | undefined {
    if (!url) return undefined;
    try {
        const parsed = new URL(url);
        return ["https:", "http:"].includes(parsed.protocol) && !parsed.username && !parsed.password ? parsed.href : undefined;
    } catch { return undefined; }
}

const VERIFICATION: Record<string, string> = { owner_statement: "Утверждение владельца", source_statement: "Сведения источника", participant_report: "Отчёт участника", status_only: "Только статус задачи", analysis: "Вывод анализа", unknown: "Подтверждение неизвестно" };

export function AgentContextReferences({ projectId, external = [], assertions = [] }: { projectId: number; external?: ExternalSourceReference[]; assertions?: AssertionReference[] }) {
    const scopedSources = external.filter(source => source.project_id === projectId);
    const scopedClaims = assertions.filter(claim => claim.project_id === projectId);
    const sources = scopedSources.filter(source => source.state === "current" || (source.kind === "web_page" && source.state === "stale"));
    const claims = scopedClaims.filter(claim => claim.state === "current" || claim.state === "conflicting");
    const unavailable = sources.length < scopedSources.length || claims.length < scopedClaims.length;
    if (!sources.length && !claims.length && !unavailable) return null;
    return <div className="mt-3 grid min-w-0 gap-2" aria-label="Источники контекста Ариса">
        {unavailable && <p className="text-[12px] text-muted">Часть источников ответа изменилась или недоступна. Прежние фрагменты скрыты.</p>}
        {sources.map(source => <details key={`${source.source_id}-${source.content_hash ?? source.commit_sha ?? ""}`} className="brand-entity-card min-w-0 rounded-md border border-line p-3 text-[12px]">
            <summary className="touch-target cursor-pointer break-words font-medium text-secondary">{source.title} · {source.kind === "git_file" ? "Исходный код" : source.kind === "git_change" ? "Изменение и подтверждения" : source.coverage === "search_snippet" ? "Поисковый фрагмент" : "Прочитанная веб-страница"}</summary>
            {(source.kind === "git_file" || source.kind === "git_change") && <p className="mt-2 break-all font-mono text-muted">Репозиторий {source.repository_id} · {source.commit_sha}{source.path && ` · ${source.path}`}{source.line_start != null && ` · строки ${source.line_start}–${source.line_end ?? source.line_start}`}</p>}
            {source.kind === "web_page" && <p className="mt-2 text-muted">{source.state === "stale" ? "Сохранённое наблюдение; актуальность страницы не перепроверена." : source.coverage === "search_snippet" ? "Страница не прочитана; доступен только поисковый фрагмент." : source.coverage === "truncated_text" ? "Прочитана ограниченная текстовая проекция." : "Прочитана текстовая проекция страницы."}{source.fetched_at && <> Получено: <time dateTime={source.fetched_at}>{new Date(source.fetched_at).toLocaleString("ru-RU")}</time>.</>}</p>}
            {source.excerpt && source.state === "current" && <blockquote className="mt-2 whitespace-pre-wrap break-words border-l-2 border-line pl-3 text-secondary">{source.excerpt}</blockquote>}
            {source.content_hash && <p className="mt-2 break-all font-mono text-[11px] text-muted">SHA-256: {source.content_hash}</p>}
            {publicLink(source.url) && <a href={publicLink(source.url)} target="_blank" rel="noopener noreferrer" className="mt-2 inline-flex min-h-11 items-center text-accent hover:underline">Открыть источник</a>}
        </details>)}
        {claims.map(claim => <details key={`${claim.claim_id}-${claim.revision}`} className="brand-entity-card min-w-0 rounded-md border border-line p-3 text-[12px]">
            <summary className="touch-target cursor-pointer text-secondary">Утверждение карты · версия {claim.revision}</summary>
            <p className="mt-2 text-muted">{VERIFICATION[claim.verification] ?? claim.verification}</p>
            <p className="mt-2 whitespace-pre-wrap break-words text-secondary">{claim.text}</p>
            <p className="mt-2 break-words text-muted">Источники: {claim.source_ids.join(", ")}</p>
        </details>)}
    </div>;
}
