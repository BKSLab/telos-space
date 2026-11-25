import { useEffect, useMemo, useRef, useState } from "react";
import { Background, Controls, ReactFlow, ReactFlowProvider, useEdgesState, useNodesState, type Edge, type Node, type ReactFlowInstance } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Button } from "@/components/ui/Button";
import type { GitConnection, GitGraph, GitSymbol } from "@/lib/projectKnowledge";
import { repositoryGraphModel, type GraphNodeData } from "./repositoryGraphModel";
import { layoutRepository } from "./repositoryLayout";

export type RepositoryLayout = "hierarchy" | "layers" | "radial";
export type RepositoryOverlay = "structure" | "changes" | "risks" | "dependencies";
function GraphCanvas({ graph, name, layout, overlay, selected, onSelect, onPath }: { graph: GitGraph; name: string; layout: RepositoryLayout; overlay: RepositoryOverlay; selected: number; onSelect: (symbol: GitSymbol) => void; onPath: (path: string) => void }) {
    const model = useMemo(() => repositoryGraphModel(graph, name), [graph, name]);
    const [nodes, setNodes, onNodesChange] = useNodesState(model.nodes);
    const [edges, setEdges, onEdgesChange] = useEdgesState(model.edges);
    const [layoutError, setLayoutError] = useState("");
    const initial = useRef(true);
    const [layoutEpoch, setLayoutEpoch] = useState(0);
    const root = useRef<HTMLDivElement>(null);
    const [instance, setInstance] = useState<ReactFlowInstance<Node<GraphNodeData>, Edge> | null>(null);
    const instanceRef = useRef(instance);
    useEffect(() => { instanceRef.current = instance; }, [instance]);
    const identity = model.nodes.map(node => node.id).join(",") + model.edges.map(edge => `${edge.source}:${edge.target}`).join(",");
    useEffect(() => {
        return layoutRepository({ nodes: model.nodes.map(node => ({ id: node.id, label: node.data.label })), edges: model.edges, mode: layout }, result => {
            const positions = new Map(result.map(position => [position.id, { x: position.x, y: position.y }]));
            const fit = initial.current;
            setNodes(current => model.nodes.map(node => { const previous = current.find(item => item.id === node.id); return { ...node, selected: previous?.selected, className: previous?.className ?? node.className, position: !fit && previous?.position || positions.get(node.id) || node.position }; }));
            initial.current = false;
            if (fit) requestAnimationFrame(() => void instanceRef.current?.fitView({ padding: 0.2 }));
        }, () => setLayoutError("Карта недоступна. Используйте таблицу."));
        // The identity is independent of overlays, selection and refresh object identity.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [identity, layout, layoutEpoch, setNodes]);
    useEffect(() => { initial.current = true; }, [layout]);
    useEffect(() => {
        setEdges(model.edges.map(edge => ({ ...edge, style: { ...edge.style, opacity: overlay === "dependencies" && edge.id.startsWith("contains:") ? 0.25 : 1 } })));
        setNodes(current => current.map(node => ({ ...node, selected: node.data.symbol?.id === selected,
            className: `repository-map-node ${overlay === "changes" && graph.task_links?.some(link => link.path === node.data.symbol?.path) ? "repository-changed-node" : ""}`,
        })));
    }, [model, overlay, selected, graph.task_links, setEdges, setNodes]);
    return <div ref={root} className="relative flex min-h-96 min-w-0 flex-1 flex-col rounded-md border border-line-subtle bg-surface-2">
        <div className="flex flex-wrap gap-2 border-b border-line-subtle p-2">
            <Button size="sm" onClick={() => void instance?.fitView({ padding: 0.2 })}>Вписать</Button>
            <Button size="sm" onClick={() => { initial.current = true; setLayoutEpoch(value => value + 1); }}>Сбросить</Button>
            <Button size="sm" onClick={() => { if (document.fullscreenElement) void document.exitFullscreen(); else void root.current?.requestFullscreen?.(); }}>Полный экран</Button>
        </div>
        {layoutError && <p role="status" className="p-2 text-[12px] text-warning">{layoutError}</p>}
        <div className="min-h-96 flex-1"><ReactFlow<Node<GraphNodeData>, Edge> nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onInit={setInstance}
            onNodeClick={(_, node) => node.data.symbol ? onSelect(node.data.symbol as GitSymbol) : node.id.startsWith("path:") && onPath(node.id.slice(5))}
            nodesConnectable={false} edgesReconnectable={false} minZoom={0.1} maxZoom={2} fitView>
            <Background color="var(--color-line-subtle)" gap={24} /><Controls showInteractive={false} />
        </ReactFlow></div>
        <p className="p-2 text-[11px] text-muted">Содержит — иерархия. Стрелка — зависимость; пунктир — candidate / unresolved. Перетаскивание меняет только вид карты.</p>
        {model.bounded && <p className="p-2 text-[12px] text-warning">Показана ограниченная область: не более 100 узлов и 500 связей. Выберите символ и раскройте его окружение.</p>}
    </div>;
}

export default function RepositoryGraph(props: React.ComponentProps<typeof GraphCanvas>) {
    const [table, setTable] = useState(false);
    return <section className="flex min-h-0 min-w-0 flex-1 flex-col gap-2" aria-label="Карта кода">
        <Button size="sm" className="self-start" aria-pressed={table} onClick={() => setTable(!table)}>{table ? "Графическая карта" : "Табличная карта"}</Button>
        {table ? <div className="overflow-auto"><table className="w-full text-left text-[12px]"><caption className="text-muted">Узлы и доказательства связей выбранной области</caption><thead><tr><th>Символ</th><th>Путь / строки</th><th>Связи</th></tr></thead><tbody>{props.graph.nodes.map(node => <tr key={node.id} className="border-t border-line-subtle align-top"><td><Button size="sm" onClick={() => props.onSelect(node)}>{node.qualified_name}</Button></td><td className="break-all">{node.path}:{node.line_start}–{node.line_end}</td><td>{props.graph.edges.filter(edge => edge.source_id === node.id || edge.target_id === node.id).map(edge => <p key={edge.id}>{edge.source_id} → {edge.target_id ?? edge.label} · {edge.kind} · {edge.confidence} · {edge.path}:{edge.line_start}–{edge.line_end}</p>)}</td></tr>)}</tbody></table></div>
            : <ReactFlowProvider><GraphCanvas {...props} /></ReactFlowProvider>}
    </section>;
}

/** Verified independent roots on the current repository page; expansion always selects its own SHA. */
export function RepositoryRoots({ connections, layout, onSelect }: { connections: GitConnection[]; layout: RepositoryLayout; onSelect: (id: number) => void }) {
    const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
    const identity = connections.map(item => `${item.id}:${item.observed_sha}`).join(",");
    useEffect(() => {
        const next = connections.map(item => ({ id: String(item.id), position: { x: 0, y: 0 }, className: "repository-map-node", data: { label: `${item.name}\n${item.ref}\n${(item.observed_sha ?? item.indexed_sha)?.slice(0, 12) ?? "Нет версии"}` } }));
        return layoutRepository({ nodes: next.map(node => ({ id: node.id, label: node.data.label })), edges: [], mode: layout }, result => { const positions = new Map(result.map(item => [item.id, item])); setNodes(next.map(node => ({ ...node, position: positions.get(node.id) ?? node.position }))); }, () => {});
        // The page identity includes every repository's independently verified SHA.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [identity, layout, setNodes]);
    return <div className="flex min-h-[420px] min-w-0 flex-col rounded-md border border-line-subtle bg-surface-2"><p className="p-3 text-[12px] text-muted">Корни доступных репозиториев этой страницы. Выберите корень для раскрытия его структуры. Общего SHA и неподтверждённых связей нет.</p><div className="h-[420px] shrink-0"><ReactFlowProvider><ReactFlow nodes={nodes} edges={[]} onNodesChange={onNodesChange} onNodeClick={(_, node) => onSelect(Number(node.id))} nodesConnectable={false} fitView minZoom={0.1}><Background /><Controls showInteractive={false} /></ReactFlow></ReactFlowProvider></div></div>;
}
