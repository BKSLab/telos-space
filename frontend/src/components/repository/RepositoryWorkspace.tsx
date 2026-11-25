import { lazy, Suspense, useCallback, useEffect, useRef, useState, type CSSProperties, type PointerEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { createPortal } from "react-dom";
import { Drawer } from "@/components/ui/Drawer";
import { Code2, Files, GitBranch, GitCompareArrows, Layers, Network, Orbit, PanelRight, RefreshCw, Search } from "lucide-react";
import { Button, LinkButton } from "@/components/ui/Button";
import { ErrorMessage } from "@/components/ui/States";
import { api, ApiError } from "@/lib/api";
import { contextApi, GIT_METADATA_REASONS, type GitConnection, type GitConnectionPage, type GitGraph, type GitSnapshotInfo, type GitSourceHit, type GitSymbol, type GitTreeEntry, type GitTreePage } from "@/lib/projectKnowledge";
import { GitIndexBadge, GitIndexDetails, GitIndexSummary } from "@/components/projects/GitIndexStatus";
import { RepositoryAccess, repositoryPrefix, repositoryKey, repositoryPath, useRepositoryQuery } from "./repositoryApi";
import { RepositoryTree } from "./RepositoryTree";
import { SourceViewer, type SourceSelection } from "./SourceViewer";
import { RepositorySearch } from "./RepositorySearch";
import { RepositoryInspector } from "./RepositoryInspector";
import { RepositoryChanges } from "./RepositoryChanges";
import type { RepositoryLayout, RepositoryOverlay } from "./RepositoryGraph";
import "./repository.css";

const RepositoryGraph = lazy(() => import("./RepositoryGraph"));
const RepositoryRoots = lazy(() => import("./RepositoryGraph").then(module => ({ default: module.RepositoryRoots })));
const modes = [{ value: "files", label: "Файлы", icon: Files }, { value: "hierarchy", label: "Иерархия", icon: Network }, { value: "layers", label: "Слои", icon: Layers }, { value: "radial", label: "Радиальная", icon: Orbit }, { value: "changes", label: "Изменения", icon: GitCompareArrows }, { value: "search", label: "Поиск", icon: Search }];
const positive = (value: string | null, fallback = 0) => value && /^\d+$/.test(value) && Number.isSafeInteger(Number(value)) ? Math.max(fallback, Number(value)) : fallback;

export function RepositoryWorkspace({ projectId }: { projectId: number }) {
    const client = useQueryClient();
    const [params, setParams] = useSearchParams();
    const [revoked, setRevoked] = useState<number[]>([]);
    const revoke = useCallback((id: number) => {
        setRevoked(current => current.includes(id) ? current : [...current, id]);
        const queryKey = id ? repositoryKey(projectId, id) : repositoryPrefix(projectId);
        void client.cancelQueries({ queryKey }).then(() => client.removeQueries({ queryKey }));
        client.removeQueries({ queryKey: repositoryKey(projectId, "all") });
        client.removeQueries({ queryKey: repositoryKey(projectId, "connections") });
    }, [client, projectId]);
    const connectionId = positive(params.get("connection"));
    const offset = positive(params.get("repo_offset"));
    const previousOffsets = useRef(new Map<number, number>());
    const connections = useQuery({ queryKey: [...repositoryKey(projectId, "connections"), offset], enabled: !revoked.includes(0), queryFn: async ({ signal }) => {
        try { return await api.get<GitConnectionPage>(`${contextApi.git(projectId)}/connections?offset=${offset}&limit=10&verified=true`, { signal }); }
        catch (error) { if (error instanceof ApiError && [401, 403].includes(error.status)) revoke(0); throw error; }
    }, retry: false, refetchInterval: 15000 });
    const selectedConnection = useQuery({ queryKey: [...repositoryKey(projectId, connectionId), "connection"], enabled: connectionId > 0 && !revoked.includes(0) && !revoked.includes(connectionId) && !connections.error, retry: false,
        queryFn: async ({ signal }) => { try { const item = await api.get<GitConnection>(repositoryPath(projectId, connectionId), { signal }); if (item.state === "access_lost") revoke(connectionId); return item; } catch (error) { if (error instanceof ApiError && [401, 403].includes(error.status)) revoke(connectionId); throw error; } }, refetchInterval: 15000,
    });
    const connection = selectedConnection.data ?? connections.data?.items.find(item => item.id === connectionId);
    const denied = revoked.includes(0) || revoked.includes(connectionId) || connection?.state === "access_lost" || !connection?.enabled;
    const update = useCallback((values: Record<string, string | number | null>, replace = false) => {
        setParams(current => { const next = new URLSearchParams(current); next.set("view", "code"); for (const [key, value] of Object.entries(values)) { if (value === null || value === "") next.delete(key); else next.set(key, String(value)); } return next; }, { replace });
    }, [setParams]);
    const selectConnection = (id: number) => update({ connection: id || null, sha: null, path: null, symbol: null, line: null, end: null, source_offset: null, directory: null, root: null });
    const suppliedSha = params.get("sha");
    const sha = suppliedSha && /^[0-9a-f]{40}$/.test(suppliedSha) ? suppliedSha : connection?.observed_sha ?? connection?.indexed_sha ?? "";
    // Pin the first observed SHA in this history entry. Later index refreshes do not silently switch the source.
    useEffect(() => { if (connectionId && sha && suppliedSha !== sha) update({ sha }, true); }, [connectionId, sha, suppliedSha, update]);
    const mode = modes.some(item => item.value === params.get("code_view")) ? params.get("code_view")! : "files";
    const selection: SourceSelection = { connection: connectionId, sha, path: params.get("path") ?? "", line: positive(params.get("line"), 1), end: positive(params.get("end"), positive(params.get("line"), 1)), offset: positive(params.get("source_offset")) };
    const symbolId = positive(params.get("symbol"));
    const [pane, setPane] = useState("source");
    const [metadata, setMetadata] = useState<{ entry: GitTreeEntry; connection: number; sha: string } | null>(null);
    const [overlay, setOverlay] = useState<RepositoryOverlay>("structure");
    const [depth, setDepth] = useState(1);
    const [leftWidth, setLeftWidth] = useState(220);
    const [inspector, setInspector] = useState(false);
    const [controlsOpen, setControlsOpen] = useState(false);
    const [controlsTarget, setControlsTarget] = useState<HTMLDivElement | null>(null);
    const [inspectorTarget, setInspectorTarget] = useState<HTMLDivElement | null>(null);
    const root = useRef<HTMLDivElement>(null);
    const onQuery = useCallback((query: string) => update({ code_q: query }, true), [update]);
    const selectSymbol = (symbol: GitSymbol) => { setMetadata(null); update({ path: symbol.path, symbol: symbol.id, line: symbol.line_start, end: symbol.line_end, source_offset: null, directory: symbol.path.split("/").slice(0, -1).join("/") }); setInspector(true); setControlsOpen(true); };
    const openHit = (hit: GitSourceHit, map = false) => { setMetadata(null); update({ connection: hit.connection_id, sha: hit.commit_sha, path: hit.path, symbol: hit.symbol_id ?? null, line: hit.line_start ?? 1, end: hit.line_end ?? hit.line_start ?? 1, source_offset: null, code_view: map ? "hierarchy" : "files", directory: hit.path.split("/").slice(0, -1).join("/"), root: null }); setPane("source"); };
    function resize(event: PointerEvent<HTMLDivElement>) {
        event.preventDefault(); const start = event.clientX; const width = leftWidth;
        const move = (next: globalThis.PointerEvent) => setLeftWidth(Math.max(200, Math.min(420, width + next.clientX - start)));
        const stop = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", stop); window.removeEventListener("pointercancel", stop); };
        window.addEventListener("pointermove", move); window.addEventListener("pointerup", stop); window.addEventListener("pointercancel", stop);
    }
    const separator = <div role="separator" tabIndex={0} aria-orientation="vertical" aria-label="Ширина дерева" aria-valuemin={200} aria-valuemax={420} aria-valuenow={leftWidth}
        className="repository-separator" onPointerDown={resize} onKeyDown={event => { if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return; event.preventDefault(); setLeftWidth(current => Math.max(200, Math.min(420, current + (event.key === "ArrowRight" ? 20 : -20)))); }} />;
    const controlsButton = <Button size="sm" variant="ghost" className="repository-mode-button repository-parameters-button" aria-haspopup="dialog" aria-expanded={controlsOpen} onClick={() => { setInspector(false); setControlsOpen(true); }}><PanelRight size={21} strokeWidth={1.7} aria-hidden="true" /><span>Панель кода</span></Button>;
    return <RepositoryAccess value={revoke}><section ref={root} className="repository-workspace flex min-h-0 min-w-0 flex-1 flex-col" aria-label="Код и репозитории">
        <div className="repository-workspace-body">
            <aside aria-label="Навигация по коду" className="repository-mode-rail"><nav aria-label="Представления кода" className="repository-mode-list">{modes.map(({ value, label, icon: Icon }) => <Button key={value} size="sm" variant="ghost" className="repository-mode-button" aria-pressed={mode === value} onClick={() => { update({ code_view: value }); setControlsOpen(false); }}><Icon size={21} strokeWidth={1.7} aria-hidden="true" /><span>{label}</span></Button>)}</nav><div className="repository-mode-settings">{controlsButton}</div></aside>
            <div className="repository-mode-content">
        {connections.isPending && <p role="status" className="text-muted">Читаем доступные репозитории…</p>}
        {connections.error && <ErrorMessage message={connections.error.message} action={<Button onClick={() => void connections.refetch()}>Повторить репозитории</Button>} />}
        {connections.data?.incomplete_reasons?.length ? <p role="status" className="text-[12px] text-warning">Список доступных репозиториев неполон. Проверка источников ограничена; обновите данные для повторной проверки.</p> : null}
        {revoked.includes(0) && <p role="alert" className="text-warning">Доступ к исходникам изменился. Прочитанные данные удалены из кэша.</p>}
        {!revoked.includes(0) && !connections.error && !connectionId && <>
            {mode === "search" ? <RepositorySearch key={`all:${projectId}`} project={projectId} connection={0} sha="" value={params.get("code_q") ?? ""} onQuery={onQuery} onOpen={openHit} /> : <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto">
                <p className="mb-3 text-[13px] text-muted">У каждого репозитория собственная ветка, версия и охват. Выберите источник для файлов и карты. Межрепозиторные связи без доказательств не строятся.</p>
                {["hierarchy", "layers", "radial"].includes(mode) && connections.data && connections.data.items.length > 0 && <Suspense fallback={<p role="status">Загружаем карту репозиториев…</p>}><RepositoryRoots connections={connections.data.items} layout={mode as RepositoryLayout} onSelect={selectConnection} /></Suspense>}
                {!connections.isPending && connections.data?.total === 0 && <p className="text-muted">Репозитории не подключены. Контекст проекта доступен без Git.</p>}
                <ul className="grid min-w-0 gap-3 md:grid-cols-2">{connections.data?.items.map(item => <li key={item.id} className="surface-card min-w-0 space-y-3 rounded-lg border border-line-subtle p-4">
                    <div className="flex min-w-0 flex-wrap items-start gap-2"><GitBranch size={17} className="shrink-0 text-accent" aria-hidden="true" /><h4 className="min-w-0 flex-1 break-words font-medium text-primary">{item.name}</h4><GitIndexBadge connection={item} /></div>
                    <p className="break-all text-[12px] text-muted">{item.owner}/{item.repo} · {item.ref}</p><p className="break-all font-mono text-[11px] text-muted">Обнаружен: {item.observed_sha ?? "неизвестно"}<br />Индекс: {item.indexed_sha ?? "не построен"}</p>
                    <p className="text-[12px] text-muted">Каталог {item.coverage.cataloged_files}/{item.coverage.total_files} · текст {item.coverage.indexed_files} · метаданные {item.coverage.metadata_files}</p>
                    <details><summary className="touch-target cursor-pointer text-[12px] text-accent">Диагностика {item.name}</summary><GitIndexDetails connection={item} /></details>
                    <Button size="sm" onClick={() => selectConnection(item.id)}>Открыть {item.name}</Button>
                </li>)}</ul>
            </div>}
        </>}
        {!connections.error && connectionId > 0 && (denied ? <p role="status" className="text-[13px] text-warning">{revoked.includes(connectionId) || connection?.state === "access_lost" ? "Доступ к репозиторию потерян. Прочитанные данные удалены из кэша." : selectedConnection.isPending && !connection ? "Проверяем подключение…" : selectedConnection.error?.message ?? "Источник отключён или недоступен."}</p>
            : <RepositoryContent key={`${projectId}:${connectionId}:${sha}`} project={projectId} connection={connection!} selection={selection} symbolId={symbolId} mode={mode} directory={params.get("directory") ?? ""} rootId={positive(params.get("root"))}
                pane={pane} setPane={setPane} metadata={metadata?.entry.path === selection.path && metadata.connection === connectionId && metadata.sha === sha ? metadata.entry : null} overlay={overlay} setOverlay={setOverlay} depth={depth} setDepth={setDepth} inspector={inspector} setInspector={setInspector} controlsTarget={controlsTarget} inspectorTarget={inspectorTarget} closeControls={() => setControlsOpen(false)}
                style={{ "--repository-tree-width": `${leftWidth}px` } as CSSProperties} leftSeparator={separator}
                query={params.get("code_q") ?? ""} onQuery={onQuery} onOpen={openHit} onSymbol={selectSymbol} update={update}
                onEntry={entry => { setInspector(false); setControlsOpen(false); setMetadata(entry.index_state === "metadata" || entry.index_state === "error" ? { entry, connection: connectionId, sha } : null); update({ code_view: "files", path: entry.path, directory: entry.path.split("/").slice(0, -1).join("/"), symbol: null, line: null, end: null, source_offset: null }); setPane("source"); }} />)}

            </div>
        </div>
        <Drawer label="Панель кода" header={<h2 className="text-base font-semibold text-primary">Код и репозитории</h2>} closeLabel="Закрыть панель кода" isOpen={controlsOpen} onClose={() => setControlsOpen(false)}>
            <nav aria-label="Разделы панели кода" className="flex gap-2 border-b border-line-subtle p-4"><Button size="sm" aria-pressed={!inspector} onClick={() => setInspector(false)}>Репозиторий</Button>{connectionId > 0 && !denied && selection.path && <Button size="sm" aria-pressed={inspector} onClick={() => setInspector(true)}>Источник</Button>}</nav>
            <div hidden={inspector} className="space-y-4 p-4">
        <header className="flex flex-wrap items-center gap-2">
            <Code2 size={19} className="hidden text-accent sm:block" aria-hidden="true" /><h3 className="sr-only text-sm font-semibold text-primary sm:not-sr-only sm:mr-auto">Код и репозитории</h3>
            <label className="flex min-w-0 max-w-full flex-1 items-center gap-2 text-[12px] text-muted sm:flex-none"><span className="sr-only sm:not-sr-only">Репозиторий</span><select aria-label="Репозиторий" className="control-surface min-w-0 max-w-64 flex-1 rounded-md p-2 text-[13px] text-secondary" value={connectionId || ""} onChange={event => selectConnection(Number(event.target.value))}>
                <option value="">Все репозитории</option>{connection && !connections.data?.items.some(item => item.id === connection.id) && <option value={connection.id}>{connection.name}</option>}{connections.data?.items.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select></label>
            <Button size="sm" aria-label="Обновить данные" icon={<RefreshCw size={14} aria-hidden="true" />} disabled={connections.isFetching} onClick={() => { setRevoked([]); void client.invalidateQueries({ queryKey: repositoryPrefix(projectId) }); }}><span className="hidden sm:inline">Обновить данные</span></Button>
            <LinkButton size="sm" to="../settings">Управление</LinkButton>
        </header>
                {!connectionId && connections.data && !connections.error && !revoked.includes(0) && <GitIndexSummary page={connections.data} />}
                <div ref={setControlsTarget} className="space-y-4" />
        {connections.data && !connections.error && (offset > 0 || connections.data.next_offset !== null) && <nav aria-label="Страницы репозиториев" className="flex shrink-0 flex-wrap items-center gap-2 text-[12px] text-muted"><span>На странице: {connections.data.items.length}{connections.data.total !== null ? ` из ${connections.data.total}` : ""}</span><Button size="sm" disabled={!offset} onClick={() => update({ repo_offset: previousOffsets.current.get(offset) ?? 0 })}>Назад</Button><Button size="sm" disabled={connections.data.next_offset === null} onClick={() => { const next = connections.data?.next_offset; if (next != null) { previousOffsets.current.set(next, offset); update({ repo_offset: next }); } }}>Дальше</Button></nav>}
            </div>
            <div hidden={!inspector} ref={setInspectorTarget} className="p-4" />
        </Drawer>
    </section></RepositoryAccess>;
}

interface ContentProps {
    project: number; connection: GitConnection; selection: SourceSelection; symbolId: number; mode: string; directory: string; rootId: number;
    pane: string; setPane: (value: string) => void; metadata: GitTreeEntry | null; overlay: RepositoryOverlay; setOverlay: (value: RepositoryOverlay) => void;
    depth: number; setDepth: (value: number) => void; inspector: boolean; setInspector: (value: boolean) => void; style: CSSProperties;
    leftSeparator: React.ReactNode; closeControls: () => void; controlsTarget: HTMLElement | null; inspectorTarget: HTMLElement | null; query: string; onQuery: (query: string) => void; onOpen: (hit: GitSourceHit, map?: boolean) => void;
    onSymbol: (symbol: GitSymbol) => void; update: (values: Record<string, string | number | null>, replace?: boolean) => void; onEntry: (entry: GitTreeEntry) => void;
}
function RepositoryContent(p: ContentProps) {
    const { project, connection, selection, mode, update } = p;
    const [snapshotOffset, setSnapshotOffset] = useState(0);
    const snapshots = useRepositoryQuery<{ items: GitSnapshotInfo[]; next_offset: number | null }>({ project, connection: connection.id, sha: selection.sha, kind: "snapshots", params: { offset: String(snapshotOffset), limit: "20" } });
    const isGraph = ["hierarchy", "layers", "radial"].includes(mode);
    const [catalogPage, setCatalogPage] = useState({ directory: p.directory, offset: 0 });
    const catalogOffset = catalogPage.directory === p.directory ? catalogPage.offset : 0;
    const catalog = useRepositoryQuery<GitTreePage>({ project, connection: connection.id, sha: selection.sha, kind: "tree", params: { children: "true", prefix: p.directory, offset: String(catalogOffset), limit: "40" }, enabled: isGraph });
    const graph = useRepositoryQuery<GitGraph>({ project, connection: connection.id, sha: selection.sha, kind: "graph", params: { depth: String(p.depth), limit: "100", direction: "both", relation: "all", ...(p.rootId ? { root_id: String(p.rootId) } : {}) }, enabled: isGraph });
    const impact = useRepositoryQuery<GitGraph>({ project, connection: connection.id, sha: selection.sha, kind: "impact", params: { symbol_id: String(p.symbolId), depth: "1", limit: "100" }, enabled: isGraph && p.symbolId > 0 });
    const coverage = snapshots.data?.items.find(item => item.commit_sha === selection.sha);
    const historical = Boolean(selection.sha && connection.observed_sha && selection.sha !== connection.observed_sha);
    const incompleteCatalog = coverage?.coverage.reasons.some(reason => ["tree_limit", "upstream_directory_truncated"].includes(reason));
    return <>
        {p.controlsTarget && createPortal(<>
        <div className="flex shrink-0 flex-wrap items-center gap-2 text-[12px] text-muted"><GitIndexBadge connection={connection} /><span className="break-all">{connection.ref}</span>
            <label className="flex min-w-0 max-w-full items-center gap-2">Версия<select aria-label="Версия репозитория" title={selection.sha} className="control-surface min-w-0 max-w-64 rounded p-1 font-mono" value={selection.sha} onChange={event => update({ sha: event.target.value, symbol: null, root: null, source_offset: null })}>
                {!snapshots.data?.items.some(item => item.commit_sha === selection.sha) && <option value={selection.sha}>{selection.sha.slice(0, 8) || "Нет версии"}</option>}{snapshots.data?.items.map(item => <option key={item.commit_sha} value={item.commit_sha}>{item.commit_sha.slice(0, 8)} · {item.analysis_state === "published" ? "готова" : item.analysis_state}</option>)}
            </select></label>
            {snapshotOffset > 0 && <Button size="sm" onClick={() => setSnapshotOffset(Math.max(0, snapshotOffset - 20))}>Новые версии</Button>}{snapshots.data?.next_offset != null && <Button size="sm" onClick={() => setSnapshotOffset(snapshots.data!.next_offset!)}>Предыдущие версии</Button>}

        </div>
        {coverage && <dl className="repository-metrics" aria-label="Покрытие версии">{[
            ["Файлы", coverage.coverage.total_files], ["Текст", coverage.coverage.indexed_files], ["Структура", coverage.coverage.analyzed_files], ["Символы", coverage.coverage.symbols],
        ].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{Number(value).toLocaleString("ru")}</dd></div>)}</dl>}
        {snapshots.error && <ErrorMessage message={snapshots.error.message} />}
        {historical && <div role="status" className="flex shrink-0 flex-wrap items-center gap-2 text-[12px] text-muted"><span>Открыта сохранённая версия <span className="font-mono">{selection.sha.slice(0, 8)}</span>. Ветка уже обновилась.</span><Button size="sm" onClick={() => update({ sha: connection.observed_sha!, symbol: null, root: null, line: null, end: null, source_offset: null })}>К актуальной версии</Button></div>}
        {coverage && !coverage.complete && <p role="status" className="shrink-0 text-[12px] text-warning">{incompleteCatalog ? "GitHub вернул неполный каталог этой версии. Некоторые пути могут отсутствовать." : coverage.coverage.failed_files > 0 ? `Не удалось прочитать файлов: ${coverage.coverage.failed_files}. Поиск по этой версии может находить не всё.` : "Индексация этой версии ещё не завершена. Часть файлов пока недоступна для поиска."}</p>}
        {isGraph && coverage && coverage.analysis_state !== "published" && <p role="status" className="shrink-0 text-[12px] text-muted">Структура этой версии ещё не готова. Доступные файлы можно открыть в каталоге.</p>}
        </>, p.controlsTarget)}
        {p.inspectorTarget && p.inspector && createPortal(<RepositoryInspector key={`${selection.path}:${selection.sha}`} project={project} selection={selection} symbolId={p.symbolId} onSelect={p.onSymbol} onExpand={() => { update({ code_view: "hierarchy", root: p.symbolId }); p.setPane("source"); p.closeControls(); }} />, p.inspectorTarget)}
        <nav aria-label="Панели кода" className="repository-pane-tabs flex shrink-0 gap-2">{[{ id: "tree", label: "Дерево" }, { id: "source", label: isGraph ? "Карта" : "Содержимое" }].map(pane => <Button size="sm" key={pane.id} aria-pressed={p.pane === pane.id} onClick={() => p.setPane(pane.id)}>{pane.label}</Button>)}</nav>
        <div className="repository-panes" data-pane={p.pane} style={p.style}>
            <div className="repository-tree scrollbar-thin min-h-0 min-w-0 overflow-y-auto rounded-md border border-line-subtle p-3"><RepositoryTree project={project} connection={connection.id} sha={selection.sha} directory={p.directory} selectedPath={selection.path} onDirectory={directory => update({ directory })} onSelect={p.onEntry} /></div>
            {p.leftSeparator}
            <div role="region" aria-label="Область кода" className={`repository-source ${mode === "files" ? "repository-file-content" : ""} scrollbar-thin flex min-h-0 min-w-0 flex-col gap-3 overflow-auto rounded-md border border-line-subtle p-3`}>
                {mode === "search" ? <RepositorySearch key={`${connection.id}:${selection.sha}`} project={project} connection={connection.id} sha={selection.sha} value={p.query} onQuery={p.onQuery} onOpen={p.onOpen} />
                    : mode === "changes" ? <RepositoryChanges project={project} connection={connection.id} sha={selection.sha} snapshots={snapshots.data?.items ?? []} onOpen={(path, sha) => update({ path, sha, code_view: "files", line: null, end: null, source_offset: null })} />
                    : isGraph ? <>
                        {p.controlsTarget && createPortal(<div className="space-y-3">
                        <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted"><label>Акценты <select aria-label="Акценты карты" className="control-surface rounded p-1" value={p.overlay} onChange={event => p.setOverlay(event.target.value as RepositoryOverlay)}><option value="structure">Структура</option><option value="changes">Изменения</option><option value="risks">Риски</option><option value="dependencies">Зависимости</option></select></label><label>Глубина <select aria-label="Глубина карты" className="control-surface rounded p-1" value={p.depth} onChange={event => p.setDepth(Number(event.target.value))}>{[1, 2, 3].map(depth => <option key={depth}>{depth}</option>)}</select></label>{p.rootId > 0 && <Button size="sm" onClick={() => update({ root: null })}>Корень карты</Button>}</div>
                        {mode === "layers" && <p className="text-[12px] text-muted">Группы каталогов в доступной области. Выберите папку слева, чтобы уточнить карту.</p>}
                        {p.overlay === "risks" && <p className="text-[12px] text-muted">Оценки рисков кода не предоставлены. Отсутствие оценки не означает отсутствие рисков.</p>}
                        {p.overlay === "changes" && <p className="text-[12px] text-muted">Явные связи изменений с задачами доступны в сведениях выбранного символа. Для полного diff откройте «Изменения».</p>}
                        </div>, p.controlsTarget)}
                        {catalog.error && <ErrorMessage message={catalog.error.message} />}
                        {catalog.data && !catalog.error && (catalogOffset > 0 || catalog.data.next_offset !== null) && <nav aria-label="Страницы каталога карты" className="flex gap-2"><Button size="sm" disabled={!catalogOffset} onClick={() => setCatalogPage({ directory: p.directory, offset: Math.max(0, catalogOffset - 40) })}>Предыдущие файлы</Button><Button size="sm" disabled={catalog.data.next_offset === null} onClick={() => catalog.data?.next_offset != null && setCatalogPage({ directory: p.directory, offset: catalog.data.next_offset })}>Следующие файлы</Button></nav>}
                        {graph.isFetching && <p role="status" className="text-muted">Читаем структурную область…</p>}{graph.error && <ErrorMessage message={graph.error.message} />}
                        {graph.data && !graph.error && <Suspense fallback={<p role="status">Загружаем карту…</p>}><RepositoryGraph graph={{ ...graph.data, task_links: !impact.error ? impact.data?.task_links : undefined }} name={connection.name} catalogEntries={!catalog.error ? catalog.data?.items : undefined} catalogIncomplete={Boolean(catalog.data?.truncated || catalogOffset > 0 || catalog.data?.next_offset != null)} focusPath={p.directory} layout={mode as RepositoryLayout} overlay={p.overlay} selected={p.symbolId} onSelect={p.onSymbol} onPath={path => { const entry = catalog.data?.items.find(item => item.path === path); if (entry && entry.type !== "tree") p.onEntry(entry); else if (!entry && graph.data.nodes.some(node => node.path === path)) { update({ path, code_view: "files", symbol: null, line: null, end: null, source_offset: null, directory: path.split("/").slice(0, -1).join("/") }); p.setPane("source"); } else update({ directory: path }); }} /></Suspense>}
                    </> : <>
                        {p.metadata ? <div className="space-y-2 p-3 text-[13px] text-secondary"><h3 className="break-all font-mono">{p.metadata.path}</h3><p>{p.metadata.index_state === "error" ? "Ошибка чтения" : "Только метаданные"}: {GIT_METADATA_REASONS[p.metadata.index_reason ?? ""] ?? p.metadata.index_reason ?? "Содержимое не представлено текстом"}</p><p className="break-all font-mono text-[12px] text-muted">{selection.sha}</p></div>
                            : selection.path ? <SourceViewer key={`${selection.sha}:${selection.path}`} project={project} selection={selection} showVersionNotice={!connection.observed_sha} showCoverageNotice={!coverage} onPage={source_offset => update({ source_offset })} onRange={({ line, end }) => update({ line, end, symbol: null }, true)} /> : <div className="m-auto flex max-w-md flex-col items-center gap-3 py-8 text-center text-[13px] text-muted"><Search size={28} aria-hidden="true" /><p>Выберите файл в каталоге или дереве. Код откроется с подсветкой и номерами строк.</p></div>}
                    </>}
            </div>

        </div>
    </>;
}
