import { useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/Button";
import { ErrorMessage } from "@/components/ui/States";
import type { GitGraph, GitSymbol, GitStructureCoverage } from "@/lib/projectKnowledge";
import { useRepositoryQuery } from "./repositoryApi";
import type { SourceSelection } from "./SourceViewer";
import { useCurrentUser } from "@/lib/useAuth";
import { useArisWidgetStore } from "@/stores/arisWidget";
import { useChatDockStore } from "@/stores/chatDock";
import { api, endpoints } from "@/lib/api";
import type { AgentConversationPage } from "@/lib/types";

export function RepositoryInspector({ project, selection, symbolId, onSelect, onExpand }: { project: number; selection: SourceSelection; symbolId: number; onSelect: (symbol: GitSymbol) => void; onExpand: () => void }) {
    const [offset, setOffset] = useState(0);
    const user = useCurrentUser().data;
    const [draftNotice, setDraftNotice] = useState("");
    const symbols = useRepositoryQuery<{ items: GitSymbol[]; next_offset: number | null; analyzer_version: string | null; coverage: GitStructureCoverage }>({ project, connection: selection.connection, sha: selection.sha,
        kind: "symbols", params: { path: selection.path, offset: String(offset), limit: "30" }, enabled: Boolean(selection.path) });
    const impact = useRepositoryQuery<GitGraph>({ project, connection: selection.connection, sha: selection.sha, kind: "impact", params: { symbol_id: String(symbolId), depth: "1", limit: "100" }, enabled: symbolId > 0 });
    const selected = symbols.data?.items.find(symbol => symbol.id === symbolId);
    return <aside aria-label="Сведения об источнике" className="min-w-0 space-y-4 text-[12px]">
        <div className="space-y-1"><h3 className="text-sm font-semibold text-primary">Источник и доказательства</h3>
            <p className="break-all text-secondary">{selection.path || "Выберите файл или символ на карте"}</p>
            <p className="break-all font-mono text-muted">{selection.sha}</p>
            {selection.path && <p className="text-muted">Строки {selection.line}–{selection.end}</p>}
            {selection.path && user && <Button size="sm" onClick={() => { void (async () => {
                const state = useArisWidgetStore.getState();
                let conversation = state.conversations[user.id];
                if (!conversation) { const page = await api.get<AgentConversationPage>(`${endpoints.arisConversations()}?offset=0&limit=30`); conversation = page.items[0]?.id; }
                if (conversation) state.select(user.id, conversation);
                const key = `${user.id}:${conversation ?? "new"}`;
                const previous = state.drafts[key]?.content ?? "";
                const question = `Арис, объясни этот компонент и его зависимости. Проверь исходник: project_id=${project}, connection_id=${selection.connection}, SHA=${selection.sha}, path=${selection.path}, строки ${selection.line}–${selection.end}${symbolId ? `, symbol_id=${symbolId}` : ""}.`;
                state.updateDraft(key, { content: `${previous}${previous ? "\n\n" : ""}${question}`.slice(0, 8000) });
                const dock = useChatDockStore.getState(); dock.selectPersonal(user.id); dock.open("personal");
                setDraftNotice("Вопрос добавлен в личный черновик Ариса. Отправьте его после проверки.");
            })().catch(() => setDraftNotice("Не удалось открыть личный черновик. Повторите попытку.")); }}>Спросить о компоненте</Button>}
            {draftNotice && <p role="status" className="text-muted">{draftNotice}</p>}
        </div>
        {symbols.error && <ErrorMessage message={symbols.error.message} />}
        {symbols.data && !symbols.error && <section className="space-y-2 border-t border-line-subtle pt-3"><h4 className="font-semibold text-secondary">Символы файла</h4>
            <p className="text-muted">Анализатор: {symbols.data.analyzer_version ?? "структурный анализ отсутствует"}. Назначение: см. исходные строки.</p>
            {symbols.data.items.length === 0 && <p className="text-muted">Символов нет в доступной части индекса. Это не доказывает отсутствие реализации.</p>}
            {symbols.data.items.map(symbol => <Button size="sm" key={symbol.id} className="w-full justify-start whitespace-normal break-all text-left" aria-pressed={symbol.id === symbolId} onClick={() => onSelect(symbol)}>{symbol.name} · {symbol.kind} · {symbol.line_start}–{symbol.line_end}</Button>)}
            {(offset > 0 || symbols.data.next_offset !== null) && <div className="flex gap-2"><Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 30))}>Предыдущие символы</Button><Button size="sm" disabled={symbols.data.next_offset === null} onClick={() => symbols.data?.next_offset != null && setOffset(symbols.data.next_offset)}>Следующие символы</Button></div>}
        </section>}
        {selected?.signature && <pre className="overflow-auto whitespace-pre-wrap break-all text-secondary">{selected.signature}</pre>}
        {symbolId > 0 && <section className="space-y-2 border-t border-line-subtle pt-3"><h4 className="font-semibold text-secondary">Зависимости и использование</h4>
            <Button size="sm" onClick={onExpand}>Раскрыть окружение на карте</Button>
            {impact.isFetching && <p role="status" className="text-muted">Читаем связи…</p>}
            {impact.error && <ErrorMessage message={impact.error.message} />}
            {impact.data && !impact.error && <>
                {impact.data.truncated && <p className="text-warning">Область зависимостей неполная.</p>}
                <ul className="space-y-2">{impact.data.edges.map(edge => <li key={edge.id} className="break-all"><p>{edge.source_id} → {edge.target_id ?? edge.label} · {edge.kind} · {edge.confidence}</p><p className="font-mono text-muted">{edge.path}:{edge.line_start}–{edge.line_end}</p></li>)}</ul>
                {impact.data.edges.length === 0 && <p className="text-muted">В этой области подтверждённых связей нет.</p>}
                <h4 className="font-semibold text-secondary">Явные связи с задачами</h4>
                {impact.data.task_links?.length ? <ul>{impact.data.task_links.map(link => <li key={`${link.task_id}:${link.change_id}`}><Link className="touch-target inline-flex text-accent underline" to={`../tasks?task=${link.task_id}`}>Задача {link.task_id}</Link> · {link.kind} · {link.provider_id}<p className="break-all text-muted">{link.path}</p></li>)}</ul> : <p className="text-muted">Явных связей в этой области нет.</p>}
                <p className="text-muted">Связь с тестом не означает его выполнение. Оценка рисков из исходников не вычисляется.</p>
            </>}
        </section>}
    </aside>;
}
