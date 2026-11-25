import { MarkerType, type Edge, type Node } from "@xyflow/react";
import type { GitGraph, GitGraphEdge, GitSymbol, GitTreeEntry } from "@/lib/projectKnowledge";

export interface GraphNodeData extends Record<string, unknown> {
    label: string;
    kind: "repository" | "directory" | "file" | "symbol" | "group";
    path: string;
    detail: string;
    cluster: string;
    symbol?: GitSymbol;
    symbolCount?: number;
    fileCount?: number;
    expanded?: boolean;
    onExpand?: () => void;
}
interface ModelOptions { focusPath?: string; expandedPaths?: ReadonlySet<string>; selected?: number; layers?: boolean; catalogEntries?: GitTreeEntry[]; catalogIncomplete?: boolean }
interface Directory { path: string; children: Map<string, Directory>; files: Map<string, GitSymbol[]> }
export const GRAPH_CARD_WIDTH = 240;
export const GRAPH_CARD_HEIGHT = 82;
const inside = (path: string, directory: string) => !directory || path === directory || path.startsWith(`${directory}/`);
export function repositoryGraphScope(graph: GitGraph, focusPath = "") {
    return graph.nodes.filter(symbol => inside(symbol.path, focusPath.replace(/\/$/, "")));
}

/** Counts and grouping describe only the received graph slice, never the entire repository. */
export function repositoryGraphModel(graph: GitGraph, name: string, options: ModelOptions = {}) {
    const focusPath = (options.focusPath ?? "").replace(/\/$/, "");
    const symbols = repositoryGraphScope(graph, focusPath);
    const root: Directory = { path: focusPath, children: new Map(), files: new Map() };
    const filePaths = new Set(symbols.map(symbol => symbol.path));
    const catalog = [...new Map((options.catalogEntries ?? []).filter(entry => entry.path !== focusPath && inside(entry.path, focusPath)).map(entry => [entry.path, entry])).values()];
    const catalogPaths = new Set(catalog.map(entry => entry.path));
    const ensureDirectory = (relative: string) => {
        let directory = root;
        for (const part of relative.split("/").filter(Boolean)) {
            const path = directory.path ? `${directory.path}/${part}` : part;
            if (!directory.children.has(path)) directory.children.set(path, { path, children: new Map(), files: new Map() });
            directory = directory.children.get(path)!;
        }
        return directory;
    };
    // The children catalog provides navigable source paths beyond the bounded symbol graph.
    for (const entry of catalog) {
        const relative = entry.path.slice(focusPath ? focusPath.length + 1 : 0);
        if (entry.type === "tree") ensureDirectory(relative);
        else ensureDirectory(relative.split("/").slice(0, -1).join("/")).files.set(entry.path, []);
    }
    for (const symbol of symbols) {
        const relative = symbol.path === focusPath ? symbol.path.split("/").at(-1)! : symbol.path.slice(focusPath ? focusPath.length + 1 : 0);
        const parts = relative.split("/");
        let directory = root;
        for (const part of parts.slice(0, -1)) {
            const path = directory.path ? `${directory.path}/${part}` : part;
            if (!directory.children.has(path)) directory.children.set(path, { path, children: new Map(), files: new Map() });
            directory = directory.children.get(path)!;
        }
        if (!directory.files.has(symbol.path)) directory.files.set(symbol.path, []);
        directory.files.get(symbol.path)!.push(symbol);
    }
    const nodes: Node<GraphNodeData>[] = [];
    const edges: Edge[] = [];
    const representatives = new Map<number, string>();
    const fileRoots = new Map<string, string>();
    const pendingCatalogPaths = new Set(catalogPaths);
    let bounded = graph.truncated;
    const selectedPath = symbols.find(symbol => symbol.id === options.selected)?.path;
    let selectedRendered = false;
    const add = (id: string, data: GraphNodeData) => {
        const selectedBranch = !!data.symbol && data.symbol.id === options.selected || !!selectedPath && inside(selectedPath, data.path);
        const catalogReserve = catalogPaths.has(data.path) ? 0 : Math.min(96, pendingCatalogPaths.size);
        const selectedReserve = selectedPath && !selectedRendered && !selectedBranch ? 2 : 0;
        const budget = 100 - catalogReserve - selectedReserve;
        if (nodes.length >= budget) { bounded = true; return false; }
        nodes.push({ id, type: "repositoryCard", position: { x: 0, y: 0 }, width: GRAPH_CARD_WIDTH, height: GRAPH_CARD_HEIGHT, ariaLabel: `${data.label} · ${data.path} · ${data.detail}`, data });
        pendingCatalogPaths.delete(data.path);
        if (data.symbol && data.symbol.id === options.selected) selectedRendered = true;
        return true;
    };
    const connect = (source: string, target: string) => edges.push({ id: `contains:${target}`, source, target, type: "smoothstep", style: { stroke: "var(--color-line-strong)", strokeWidth: 1.4 } });
    const directorySymbols = (directory: Directory): GitSymbol[] => [...directory.files.values()].flat().concat([...directory.children.values()].flatMap(directorySymbols));
    add("repo", { label: focusPath ? focusPath.split("/").at(-1)! : name, kind: "repository", path: focusPath || "/", detail: catalog.length ? `${catalog.length} объектов каталога${symbols.length ? ` · ${symbols.length} символов в срезе` : ""}` : `${filePaths.size} файлов · ${symbols.length} символов в срезе`, fileCount: filePaths.size, symbolCount: symbols.length || undefined, cluster: "" });
    function visit(directory: Directory, parent: string, depth: number, cluster: string) {
        const children = [...directory.children.values()].sort((a, b) => Number(!!selectedPath && inside(selectedPath, b.path)) - Number(!!selectedPath && inside(selectedPath, a.path)) || a.path.localeCompare(b.path));
        for (let child of children) {
            // A chain of directories carries no branching information: retain its complete real path in one card.
            while (!catalogPaths.has(child.path) && !child.files.size && child.children.size === 1) child = [...child.children.values()][0];
            const contained = directorySymbols(child);
            const id = `path:${child.path}`;
            const group = cluster || child.path;
            const paths = new Set(contained.map(symbol => symbol.path));
            if (!add(id, { label: child.path.slice(directory.path ? directory.path.length + 1 : 0), kind: "directory", path: child.path, detail: contained.length ? `${paths.size} файлов · ${contained.length} символов в срезе` : "Открыть каталог", fileCount: paths.size || undefined, symbolCount: contained.length || undefined, cluster: group })) {
                contained.forEach(symbol => representatives.set(symbol.id, parent));
                continue;
            }
            connect(parent, id);
            contained.forEach(symbol => representatives.set(symbol.id, id));
            // Keep an overview compact. Selecting a folder scopes the same received data and reveals its contents.
            if ((!catalogPaths.has(child.path) && depth < 1 && paths.size <= 3) || contained.some(symbol => symbol.id === options.selected)) visit(child, id, depth + 1, group);
        }
        const files = [...directory.files.entries()].sort(([a], [b]) => Number(b === selectedPath) - Number(a === selectedPath) || Number(catalogPaths.has(b)) - Number(catalogPaths.has(a)) || a.localeCompare(b));
        const visible = files.filter(([path], index) => index < 18 || path === selectedPath || catalogPaths.has(path));
        if (visible.length < files.length) bounded = true;
        for (const [path, members] of visible) {
            const id = `path:${path}`;
            const expanded = options.expandedPaths?.has(path) ?? false;
            if (!add(id, { label: path.split("/").at(-1)!, kind: "file", path, detail: members.length ? `${members.length} символов в срезе` : "Символы вне полученного среза", symbolCount: members.length || undefined, expanded, cluster: cluster || directory.path || "/" })) {
                members.forEach(symbol => representatives.set(symbol.id, parent));
                continue;
            }
            connect(parent, id);
            fileRoots.set(path, id);
            members.forEach(symbol => representatives.set(symbol.id, id));
            const revealed = expanded ? members.slice(0, 18) : members.filter(symbol => symbol.id === options.selected);
            if (expanded && revealed.length < members.length) bounded = true;
            // The selected ID remains a real node even when it is beyond the initial symbol window.
            const selectedSymbol = members.find(symbol => symbol.id === options.selected);
            if (selectedSymbol && !revealed.includes(selectedSymbol)) revealed.push(selectedSymbol);
            for (const symbol of revealed) {
                const symbolId = `symbol:${symbol.id}`;
                if (!add(symbolId, { label: symbol.name, kind: "symbol", path, detail: `${symbol.kind} · L${symbol.line_start}–${symbol.line_end}`, symbol, cluster: cluster || directory.path || "/" })) continue;
                representatives.set(symbol.id, symbolId);
                connect(id, symbolId);
            }
            for (const symbol of revealed) {
                const containment = edges.find(edge => edge.id === `contains:symbol:${symbol.id}`);
                if (containment && revealed.some(parentSymbol => parentSymbol.id === symbol.parent_id)) containment.source = `symbol:${symbol.parent_id}`;
            }
        }
        for (const [path, members] of files) {
            if (!fileRoots.has(path)) members.forEach(symbol => representatives.set(symbol.id, parent));
        }
    }
    visit(root, "repo", 0, "");
    const relations = new Map<string, { source: string; target: string; evidence: GitGraphEdge[] }>();
    for (const edge of graph.edges) {
        if (edge.target_id === null || ["contains", "containment"].includes(edge.kind)) continue;
        const source = representatives.get(edge.source_id), target = representatives.get(edge.target_id);
        if (!source || !target || source === target) continue;
        const key = `${source}:${target}:${edge.kind}:${edge.confidence}`;
        if (!relations.has(key)) relations.set(key, { source, target, evidence: [] });
        relations.get(key)!.evidence.push(edge);
    }
    for (const { source, target, evidence } of relations.values()) {
        const first = evidence[0];
        edges.push({ id: `relation:${first.id}`, source, target, type: "smoothstep", label: `${first.kind}${evidence.length > 1 ? ` ×${evidence.length}` : ""} · ${first.confidence}`, data: { evidence }, markerEnd: { type: MarkerType.ArrowClosed }, style: { stroke: "var(--color-accent)", strokeDasharray: ["candidate", "unresolved"].includes(first.confidence) ? "5 5" : undefined } });
    }
    // Layers are real directory containers, not inferred architecture roles. The worker sizes each row.
    if (options.layers) {
        const groups = new Map<string, Node<GraphNodeData>[]>();
        for (const node of nodes.filter(node => node.id !== "repo")) {
            const directory = node.data.kind === "directory" ? node.data.path : node.data.path.split("/").slice(0, -1).join("/") || "/";
            if (!groups.has(directory)) groups.set(directory, []);
            groups.get(directory)!.push(node);
        }
        const containers: Node<GraphNodeData>[] = [];
        for (const [path, members] of groups) {
            if (nodes.length + containers.length >= 100) { bounded = true; break; }
            const id = `group:${path}`;
            containers.push({ id, type: "repositoryGroup", position: { x: 0, y: 0 }, style: { width: 300, height: 150 }, selectable: false, draggable: false, data: { label: path, kind: "group", path, detail: "Каталог в полученном срезе", cluster: path } });
            members.forEach(node => { node.parentId = id; node.extent = "parent"; });
        }
        nodes.unshift(...containers);
    }
    return { nodes, edges: edges.slice(0, 500), bounded: bounded || edges.length > 500, fileRoots, symbols, fileCount: filePaths.size, catalogCount: catalog.length, catalogIncomplete: options.catalogIncomplete ?? false, aggregated: nodes.filter(node => node.data.symbol).length < symbols.length };
}
