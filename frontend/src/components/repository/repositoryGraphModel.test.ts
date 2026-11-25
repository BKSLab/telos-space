import { expect, it } from "vitest";
import type { GitGraph, GitGraphEdge, GitSymbol, GitTreeEntry } from "@/lib/projectKnowledge";
import { repositoryGraphModel, repositoryGraphScope } from "./repositoryGraphModel";
const symbol = (id: number): GitSymbol => ({ id, file_id: 1, path: "src/auth.py", qualified_name: `auth.f${id}`, name: `f${id}`, kind: "function", parent_id: null, signature: null, line_start: id, line_end: id + 1, offset: id, end_offset: id + 2 });
it("bounds rendered structure and keeps candidate evidence distinct from containment", () => {
    const graph = { nodes: Array.from({ length: 1000 }, (_, id) => symbol(id)), edges: [{ id: 1, source_id: 0, target_id: 1, kind: "calls", confidence: "candidate", path: "src/auth.py", line_start: 1, line_end: 1 }], truncated: false } as GitGraph;
    const result = repositoryGraphModel(graph, "Source", { expandedPaths: new Set(["src/auth.py"]) });
    expect(result.nodes.length).toBeLessThanOrEqual(100); expect(result.edges.length).toBeLessThanOrEqual(500); expect(result.bounded).toBe(true);
    expect(result.edges.find(edge => edge.id === "relation:1")?.style?.strokeDasharray).toBe("5 5");
    expect(result.edges.find(edge => edge.id.startsWith("contains:"))?.markerEnd).toBeUndefined();
});

const graphWith = (nodes: GitSymbol[], edges: GitGraphEdge[] = []) => ({ nodes, edges, truncated: false }) as GitGraph;
it("aggregates a dense file into one source card and keeps the selected published ID when expanded", () => {
    const graph = graphWith(Array.from({ length: 1000 }, (_, id) => symbol(id)));
    const overview = repositoryGraphModel(graph, "Source");
    expect(overview.nodes.length).toBe(3);
    expect(overview.nodes.find(node => node.id === "path:src/auth.py")?.data.symbolCount).toBe(1000);
    expect(overview.aggregated).toBe(true);
    expect(overview.bounded).toBe(false);
    const expanded = repositoryGraphModel(graph, "Source", { expandedPaths: new Set(["src/auth.py"]), selected: 999 });
    expect(expanded.nodes.some(node => node.id === "symbol:999")).toBe(true);
    expect(expanded.nodes.length).toBeLessThanOrEqual(100);
    expect(expanded.bounded).toBe(true);
});
it("scopes directories on a path boundary and never claims absent graph files", () => {
    const graph = graphWith([{ ...symbol(1), path: "src/api/auth.py" }, { ...symbol(2), path: "src/api_extra.py" }, { ...symbol(3), path: "frontend/main.ts" }]);
    const scoped = repositoryGraphModel(graph, "Source", { focusPath: "src/api" });
    expect(scoped.symbols.map(node => node.id)).toEqual([1]);
    expect(scoped.fileCount).toBe(1);
    expect(scoped.nodes.find(node => node.id === "repo")?.data.path).toBe("src/api");
    expect(repositoryGraphScope(graph, "missing")).toEqual([]);
    expect(repositoryGraphModel(graph, "Source", { focusPath: "missing" }).nodes).toHaveLength(1);
});
it("retains exact server evidence when dependencies are aggregated between file cards", () => {
    const nodes = [{ ...symbol(1), path: "src/a.py" }, { ...symbol(2), path: "src/b.py" }, { ...symbol(3), path: "src/a.py" }];
    const edge = { id: 4, source_id: 1, target_id: 2, kind: "calls", confidence: "candidate", label: "b", path: "src/a.py", line_start: 1, line_end: 1 } as GitGraphEdge;
    const evidence = [edge, { ...edge, id: 5, source_id: 3 }];
    const result = repositoryGraphModel(graphWith(nodes, evidence), "Source");
    const relation = result.edges.find(item => item.id === "relation:4");
    expect(relation?.source).toBe("path:src/a.py");
    expect(relation?.target).toBe("path:src/b.py");
    expect(relation?.data?.evidence).toEqual(evidence);
    expect(relation?.label).toContain("×2");
    expect(relation?.style?.strokeDasharray).toBe("5 5");
});
it("creates layer containers from actual directory paths and assigns members to existing parents", () => {
    const graph = graphWith([{ ...symbol(1), path: "backend/src/auth.py" }, { ...symbol(2), path: "frontend/main.ts" }]);
    const model = repositoryGraphModel(graph, "Source", { layers: true });
    const groups = model.nodes.filter(node => node.data.kind === "group");
    expect(groups.map(node => node.data.path).sort()).toEqual(["backend/src", "frontend"]);
    for (const node of model.nodes.filter(node => node.parentId)) expect(groups.some(group => group.id === node.parentId)).toBe(true);
    expect(model.nodes.length).toBeLessThanOrEqual(100);
});
it("keeps the selected file visible even when the node budget is reached", () => {
    const nodes = Array.from({ length: 180 }, (_, id) => ({ ...symbol(id), path: `directory-${id}/file.py` }));
    const result = repositoryGraphModel(graphWith(nodes), "Source", { selected: 179 });
    expect(result.nodes.some(node => node.id === "symbol:179")).toBe(true);
    expect(result.nodes.length).toBeLessThanOrEqual(100);
    expect(result.edges.every(edge => result.nodes.some(node => node.id === edge.source) && result.nodes.some(node => node.id === edge.target))).toBe(true);
    const rootFile = repositoryGraphModel(graphWith([...nodes, { ...symbol(999), path: "root.py" }]), "Source", { selected: 999 });
    expect(rootFile.nodes.some(node => node.id === "symbol:999")).toBe(true);
});

const entry = (path: string, type: GitTreeEntry["type"] = "blob"): GitTreeEntry => ({ path, type, sha: "source-sha", mode: type === "tree" ? "040000" : "100644", size: null, index_state: "ready", index_reason: null });
it("keeps a catalog navigable without graph symbols and does not claim unknown symbol counts are zero", () => {
    const catalogEntries = [entry("backend", "tree"), entry("frontend", "tree"), entry("README.md"), entry("pyproject.toml")];
    const model = repositoryGraphModel(graphWith([]), "Source", { catalogEntries, catalogIncomplete: true });
    expect(model.nodes).toHaveLength(5);
    for (const item of catalogEntries) {
        const node = model.nodes.find(node => node.id === `path:${item.path}`)!;
        expect(node).toBeDefined();
        expect(node.data.symbolCount).toBeUndefined();
        expect(node.data.detail).toBe(item.type === "tree" ? "Открыть каталог" : "Символы вне полученного среза");
        expect(model.edges.some(edge => edge.source === "repo" && edge.target === node.id)).toBe(true);
    }
    expect(model.catalogIncomplete).toBe(true);
    for (const node of repositoryGraphModel(graphWith([]), "Source", { catalogEntries, layers: true }).nodes.filter(node => node.parentId)) expect(node.parentId).toMatch(/^group:/);
});
it("merges graph evidence into catalog paths once and keeps catalog directory boundaries", () => {
    const graph = graphWith([{ ...symbol(1), path: "src/auth.py" }, { ...symbol(2), path: "src/auth.py" }]);
    const files = repositoryGraphModel(graph, "Source", { focusPath: "src", catalogEntries: [entry("src/auth.py"), entry("src/other.py")] });
    expect(files.nodes.filter(node => node.id === "path:src/auth.py")).toHaveLength(1);
    expect(files.nodes.find(node => node.id === "path:src/auth.py")?.data.symbolCount).toBe(2);
    expect(files.nodes.find(node => node.id === "path:src/other.py")?.data.symbolCount).toBeUndefined();
    const overview = repositoryGraphModel(graph, "Source", { catalogEntries: [entry("src", "tree")] });
    expect(overview.nodes.map(node => node.id)).toEqual(["repo", "path:src"]);
    expect(overview.nodes[1].data.symbolCount).toBe(2);
});
it("reserves the bounded node budget for catalog entries ahead of unrelated graph paths", () => {
    const graph = graphWith(Array.from({ length: 180 }, (_, id) => ({ ...symbol(id), path: `directory-${id}/file.py` })));
    const catalogEntries = Array.from({ length: 40 }, (_, id) => entry(`catalog-${id}.py`));
    const model = repositoryGraphModel(graph, "Source", { catalogEntries });
    expect(model.nodes.length).toBeLessThanOrEqual(100);
    for (const item of catalogEntries) expect(model.nodes.some(node => node.id === `path:${item.path}`)).toBe(true);
});
