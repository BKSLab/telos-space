import { MarkerType, type Edge, type Node } from "@xyflow/react";
import type { GitGraph, GitSymbol } from "@/lib/projectKnowledge";

export interface GraphNodeData extends Record<string, unknown> { label: string; symbol?: GitSymbol }

/** Containment is derived only from a source path or a published parent ID. Dependencies retain server evidence. */
export function repositoryGraphModel(graph: GitGraph, name: string) {
    const nodes: Node<GraphNodeData>[] = [{ id: "repo", position: { x: 0, y: 0 }, data: { label: name }, className: "repository-map-node" }];
    const edges: Edge[] = [];
    const known = new Set(["repo"]);
    const fileRoots = new Map<string, string>();
    for (const symbol of graph.nodes) {
        let parent = "repo";
        const parts = symbol.path.split("/");
        let path = "";
        for (const part of parts) {
            path = path ? `${path}/${part}` : part;
            const id = `path:${path}`;
            if (!known.has(id)) {
                if (nodes.length >= 99) break;
                nodes.push({ id, position: { x: 0, y: 0 }, data: { label: part }, className: "repository-map-node" }); known.add(id);
                edges.push({ id: `contains:${id}`, source: parent, target: id, type: "smoothstep", label: "содержит", style: { stroke: "var(--color-line-strong)" } });
            }
            parent = id;
        }
        fileRoots.set(symbol.path, parent);
        if (nodes.length >= 100) break;
        const id = `symbol:${symbol.id}`;
        nodes.push({ id, position: { x: 0, y: 0 }, data: { label: `${symbol.name} · ${symbol.kind}`, symbol }, className: "repository-map-node" }); known.add(id);
        edges.push({ id: `contains:${id}`, source: parent, target: id, type: "smoothstep", label: "содержит", style: { stroke: "var(--color-line-strong)" } });
    }
    for (const edge of graph.edges) {
        if (edge.target_id === null || !known.has(`symbol:${edge.source_id}`) || !known.has(`symbol:${edge.target_id}`)) continue;
        const containment = edge.kind === "contains" || edge.kind === "containment";
        edges.push({ id: `relation:${edge.id}`, source: `symbol:${edge.source_id}`, target: `symbol:${edge.target_id}`, label: `${edge.kind} · ${edge.confidence}`,
            markerEnd: containment ? undefined : { type: MarkerType.ArrowClosed },
            style: { stroke: "var(--color-accent)", strokeDasharray: ["candidate", "unresolved"].includes(edge.confidence) ? "5 5" : undefined },
        });
    }
    for (const symbol of graph.nodes) {
        if (symbol.parent_id === null || !known.has(`symbol:${symbol.parent_id}`)) continue;
        const containment = edges.find(edge => edge.id === `contains:symbol:${symbol.id}`);
        if (containment) containment.source = `symbol:${symbol.parent_id}`;
    }
    return { nodes, edges: edges.slice(0, 500), bounded: graph.truncated || nodes.filter(node => node.data.symbol).length < graph.nodes.length || edges.length > 500, fileRoots };
}
