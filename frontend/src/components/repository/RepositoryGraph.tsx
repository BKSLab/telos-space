import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Background, Controls, ReactFlow, ReactFlowProvider, useEdgesState, useNodesInitialized, useNodesState, type Edge, type Node, type ReactFlowInstance } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Button } from "@/components/ui/Button";
import type { GitConnection, GitGraph, GitSymbol, GitTreeEntry } from "@/lib/projectKnowledge";
import { GRAPH_CARD_HEIGHT, GRAPH_CARD_WIDTH, repositoryGraphModel, repositoryGraphScope, type GraphNodeData } from "./repositoryGraphModel";
import { layoutRepository } from "./repositoryLayout";
import { RepositoryGraphGroup, RepositoryGraphNode } from "./RepositoryGraphNode";
import "./repositoryGraph.css";

export type RepositoryLayout = "hierarchy" | "layers" | "radial";
export type RepositoryOverlay = "structure" | "changes" | "risks" | "dependencies";
interface GraphProps { graph: GitGraph; name: string; layout: RepositoryLayout; overlay: RepositoryOverlay; selected: number; focusPath?: string; catalogEntries?: GitTreeEntry[]; catalogIncomplete?: boolean; onSelect: (symbol: GitSymbol) => void; onPath: (path: string) => void }
const nodeTypes = { repositoryCard: RepositoryGraphNode, repositoryGroup: RepositoryGraphGroup };
// A wide graph remains readable and pannable instead of fitting into an illegible miniature.
const fitOptions = { padding: 0.16, minZoom: 0.8, maxZoom: 1, duration: 180 };
const fullFitOptions = { ...fitOptions, minZoom: 0.01 };
const catalogStates = { ready: "Индекс готов", pending: "Ожидает индексации", metadata: "Только метаданные", error: "Ошибка индексации" };
interface NodePresentation { selected: number; overlay: RepositoryOverlay; taskLinks: GitGraph["task_links"]; toggleFile: (path: string) => void }
function presentNode(node: Node<GraphNodeData>, { selected, overlay, taskLinks, toggleFile }: NodePresentation): Node<GraphNodeData> {
    return { ...node, selected: node.data.symbol?.id === selected, className: overlay === "changes" && taskLinks?.some(link => link.path === node.data.path) ? "repository-changed-node" : "", data: { ...node.data, onExpand: node.data.kind === "file" && node.data.symbolCount ? () => toggleFile(node.data.path) : undefined } };
}

function GraphCanvas({ graph, name, layout, overlay, selected, focusPath = "", catalogEntries, catalogIncomplete, onSelect, onPath }: GraphProps) {
    const [expandedPaths, setExpandedPaths] = useState<ReadonlySet<string>>(new Set());
    const toggleFile = useCallback((path: string) => setExpandedPaths(current => { const next = new Set(current); if (next.has(path)) next.delete(path); else next.add(path); return next; }), []);
    const model = useMemo(() => repositoryGraphModel(graph, name, { focusPath, expandedPaths, selected, layers: layout === "layers", catalogEntries, catalogIncomplete }), [graph, name, focusPath, expandedPaths, selected, layout, catalogEntries, catalogIncomplete]);
    const presentationRef = useRef({ model, selected, overlay, taskLinks: graph.task_links, toggleFile });
    useEffect(() => { presentationRef.current = { model, selected, overlay, taskLinks: graph.task_links, toggleFile }; }, [model, selected, overlay, graph.task_links, toggleFile]);
    const [nodes, setNodes, onNodesChange] = useNodesState(model.nodes);
    const [edges, setEdges, onEdgesChange] = useEdgesState(model.edges);
    const [layoutError, setLayoutError] = useState("");
    const [layoutEpoch, setLayoutEpoch] = useState(0);
    const [layoutReady, setLayoutReady] = useState(0);
    const root = useRef<HTMLDivElement>(null);
    const [instance, setInstance] = useState<ReactFlowInstance<Node<GraphNodeData>, Edge> | null>(null);
    const instanceRef = useRef(instance);
    const nodesInitialized = useNodesInitialized();
    useEffect(() => { instanceRef.current = instance; }, [instance]);
    const identity = `${focusPath}|${model.nodes.map(node => `${node.id}:${node.parentId ?? ""}`).join(",")}|${model.edges.filter(edge => edge.id.startsWith("contains:")).map(edge => `${edge.source}:${edge.target}`).join(",")}`;
    useEffect(() => {
        const measured = new Map(instanceRef.current?.getNodes().map(node => [node.id, node]));
        return layoutRepository({ nodes: model.nodes.map(node => ({ id: node.id, label: node.data.label, width: measured.get(node.id)?.measured?.width ?? GRAPH_CARD_WIDTH, height: measured.get(node.id)?.measured?.height ?? GRAPH_CARD_HEIGHT, parentId: node.parentId, cluster: node.data.cluster, group: node.data.kind === "group" })), edges: model.edges.filter(edge => edge.id.startsWith("contains:")), mode: layout }, result => {
            const positions = new Map(result.map(position => [position.id, position]));
            const presentation = presentationRef.current;
            setNodes(presentation.model.nodes.map(node => { const position = positions.get(node.id); return presentNode({ ...node, position: position ? { x: position.x, y: position.y } : node.position, style: node.data.kind === "group" ? { width: position?.width ?? 300, height: position?.height ?? 150 } : undefined }, presentation); }));
            setLayoutError("");
            setLayoutReady(value => value + 1);
        }, () => setLayoutError("Карта недоступна. Используйте таблицу."));
        // Selection and overlay changes do not reset a layout when its actual node IDs are unchanged.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [identity, layout, layoutEpoch, setNodes, toggleFile]);
    useEffect(() => {
        if (!layoutReady || !nodesInitialized || !instance) return;
        // Wait for React Flow to measure the committed custom cards and container dimensions.
        let measuredFrame = 0, cancelled = false;
        const frame = requestAnimationFrame(() => { measuredFrame = requestAnimationFrame(() => {
            void instance.fitView({ ...fitOptions, duration: 0 }).then(() => {
                if (cancelled || instance.getViewport().zoom > fitOptions.minZoom + 0.01) return;
                // When readability limits the fit, keep the repository visible as the navigation anchor.
                const repository = instance.getNode("repo");
                if (repository) void instance.setCenter(repository.position.x + GRAPH_CARD_WIDTH / 2, repository.position.y + 120, { zoom: fitOptions.minZoom, duration: 0 });
            });
        }); });
        return () => { cancelled = true; cancelAnimationFrame(frame); cancelAnimationFrame(measuredFrame); };
    }, [layoutReady, nodesInitialized, instance]);
    useEffect(() => {
        setEdges(model.edges.map(edge => ({ ...edge, style: { ...edge.style, opacity: overlay === "dependencies" && edge.id.startsWith("contains:") ? 0.2 : overlay !== "dependencies" && edge.id.startsWith("relation:") ? 0.35 : 1 }, label: overlay === "dependencies" ? edge.label : undefined })));
        setNodes(current => current.map(node => {
            const data = model.nodes.find(item => item.id === node.id)?.data ?? node.data;
            return presentNode({ ...node, data }, { selected, overlay, taskLinks: graph.task_links, toggleFile });
        }));
    }, [model, overlay, selected, graph.task_links, setEdges, setNodes, toggleFile]);
    return <div ref={root} className="repository-graph flex min-w-0 flex-1 flex-col" data-layout={layout} data-layout-ready={layoutReady}>
        <div className="repository-graph-toolbar">
            <span className="repository-graph-scope"><strong>{focusPath || name}</strong> · {model.catalogCount ? `${model.catalogCount} объектов каталога` : `${model.fileCount} файлов в срезе`}</span>
            {focusPath && <Button size="sm" onClick={() => onPath(focusPath.split("/").slice(0, -1).join("/"))}>На уровень выше</Button>}
            <Button size="sm" onClick={() => void instance?.fitView(fullFitOptions)}>Вписать</Button>
            <Button size="sm" onClick={() => setLayoutEpoch(value => value + 1)}>Сбросить</Button>
            <Button size="sm" onClick={() => { if (document.fullscreenElement) void document.exitFullscreen(); else void root.current?.requestFullscreen?.(); }}>Полный экран</Button>
        </div>
        {layoutError && <p role="status" className="p-2 text-[12px] text-warning">{layoutError}</p>}
        {!model.symbols.length && <p role="status" className="p-3 text-[12px] text-muted">{model.catalogCount ? "Пути доступны из каталога. Символы этой области вне полученного среза графа." : "В полученном срезе нет символов этой папки. Выберите другую область или символ в дереве."}</p>}
        <div className="repository-graph-canvas"><ReactFlow<Node<GraphNodeData>, Edge> nodeTypes={nodeTypes} nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onInit={setInstance}
            onNodeClick={(_, node) => node.data.symbol ? onSelect(node.data.symbol) : ["directory", "file"].includes(node.data.kind) ? onPath(node.data.path) : undefined}
            nodesConnectable={false} edgesReconnectable={false} minZoom={0.01} maxZoom={1} aria-label="Граф структуры полученного среза кода">
            <Background color="var(--color-line-subtle)" gap={24} size={1} /><Controls showInteractive={false} />
        </ReactFlow></div>
        <div className="repository-graph-legend"><span><i />Содержит</span><span><i className="dependency" />Зависимость</span><span><i className="candidate" />Candidate / unresolved</span><span>Папка → область · файл → код · › → символы</span></div>
        <p className="px-3 pb-2 text-[11px] text-muted">{model.symbols.length} символов в полученном срезе. Связи объединены по файлам и папкам; доказательства — в таблице. Большую карту можно перемещать и уменьшать вручную.</p>
        {model.bounded && <p className="px-3 pb-2 text-[12px] text-warning">Область ограничена: до 100 узлов и 500 связей. Часть файлов или символов скрыта; уточните папку или выберите символ в дереве.</p>}
        {model.catalogIncomplete && <p className="px-3 pb-2 text-[12px] text-muted">Показана часть каталога. Загрузите следующую страницу, чтобы увидеть остальные пути.</p>}
    </div>;
}

export default function RepositoryGraph(props: GraphProps) {
    const [table, setTable] = useState(false);
    const symbols = repositoryGraphScope(props.graph, props.focusPath);
    const symbolPaths = new Set(symbols.map(symbol => symbol.path));
    const focusPath = (props.focusPath ?? "").replace(/\/$/, "");
    // A file represented by symbol rows is not repeated as a catalog row; duplicate catalog pages merge by path.
    const catalogRows = [...new Map((props.catalogEntries ?? []).filter(entry => !symbolPaths.has(entry.path) && entry.path !== focusPath && (!focusPath || entry.path.startsWith(`${focusPath}/`))).map(entry => [entry.path, entry])).values()];
    return <section className="flex min-h-0 min-w-0 flex-1 flex-col gap-2" aria-label="Карта кода">
        <Button size="sm" className="self-start" aria-pressed={table} onClick={() => setTable(!table)}>{table ? "Графическая карта" : "Табличная карта"}</Button>
        {table ? <div className="overflow-auto"><table className="w-full text-left text-[12px]"><caption className="text-muted">Пути каталога, символы и доказательства связей{props.focusPath ? ` · ${props.focusPath}` : ""}</caption><thead><tr><th>Узел</th><th>Путь / строки</th><th>Связи / состояние</th></tr></thead><tbody>
            {catalogRows.map(entry => <tr key={`path:${entry.path}`} className="border-t border-line-subtle align-top"><td><Button size="sm" onClick={() => props.onPath(entry.path)}>{entry.path.split("/").at(-1)}</Button><p className="text-muted">{entry.type === "tree" ? "Каталог" : entry.type === "commit" ? "Подмодуль" : "Файл"}</p></td><td className="break-all">{entry.path}</td><td><p>{catalogStates[entry.index_state]}</p><p className="text-muted">{entry.type === "tree" ? "Открыть каталог" : "Символы вне полученного среза"}</p></td></tr>)}
            {symbols.map(node => <tr key={`symbol:${node.id}`} className="border-t border-line-subtle align-top"><td><Button size="sm" onClick={() => props.onSelect(node)}>{node.qualified_name}</Button><p className="text-muted">{node.kind}</p></td><td className="break-all">{node.path}:{node.line_start}–{node.line_end}</td><td>{props.graph.edges.filter(edge => edge.source_id === node.id || edge.target_id === node.id).map(edge => <p key={edge.id}>{edge.source_id} → {edge.target_id ?? edge.label} · {edge.kind} · {edge.confidence} · {edge.path}:{edge.line_start}–{edge.line_end}</p>)}</td></tr>)}
        </tbody></table>{!symbols.length && !catalogRows.length && <p role="status" className="p-3 text-muted">В полученных данных нет путей или символов этой папки.</p>}{props.catalogIncomplete && <p role="status" className="p-3 text-muted">Показана часть каталога. Загрузите следующую страницу, чтобы увидеть остальные пути.</p>}</div>
            : <ReactFlowProvider><GraphCanvas {...props} /></ReactFlowProvider>}
    </section>;
}

/** Verified independent roots on the current repository page; expansion always selects its own SHA. */
export function RepositoryRoots({ connections, layout, onSelect }: { connections: GitConnection[]; layout: RepositoryLayout; onSelect: (id: number) => void }) {
    const [nodes, setNodes, onNodesChange] = useNodesState<Node<GraphNodeData>>([]);
    const identity = connections.map(item => `${item.id}:${item.observed_sha}`).join(",");
    useEffect(() => {
        const next: Node<GraphNodeData>[] = connections.map(item => ({ id: String(item.id), type: "repositoryCard", width: GRAPH_CARD_WIDTH, height: GRAPH_CARD_HEIGHT, position: { x: 0, y: 0 }, data: { label: item.name, kind: "repository", path: item.ref, cluster: String(item.id), detail: (item.observed_sha ?? item.indexed_sha)?.slice(0, 12) ?? "Нет версии" } }));
        return layoutRepository({ nodes: next.map(node => ({ id: node.id, label: node.data.label, cluster: node.id, width: GRAPH_CARD_WIDTH, height: GRAPH_CARD_HEIGHT })), edges: [], mode: layout === "layers" ? "hierarchy" : layout }, result => { const positions = new Map(result.map(item => [item.id, item])); setNodes(next.map(node => ({ ...node, position: positions.get(node.id) ?? node.position }))); }, () => {});
        // The page identity includes every repository's independently verified SHA.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [identity, layout, setNodes]);
    return <div className="repository-graph flex min-w-0 flex-col"><p className="p-3 text-[12px] text-muted">Корни доступных репозиториев этой страницы. Выберите корень для раскрытия его структуры. Общего SHA и неподтверждённых связей нет.</p><div className="h-[420px] shrink-0"><ReactFlowProvider><ReactFlow nodes={nodes} nodeTypes={nodeTypes} edges={[]} onNodesChange={onNodesChange} onNodeClick={(_, node) => onSelect(Number(node.id))} nodesConnectable={false} fitView fitViewOptions={fitOptions} minZoom={0.01} maxZoom={1}><Background /><Controls showInteractive={false} /></ReactFlow></ReactFlowProvider></div></div>;
}
