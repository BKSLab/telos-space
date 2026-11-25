import { expect, it } from "vitest";
import type { GitGraph, GitSymbol } from "@/lib/projectKnowledge";
import { repositoryGraphModel } from "./repositoryGraphModel";
const symbol = (id: number): GitSymbol => ({ id, file_id: 1, path: "src/auth.py", qualified_name: `auth.f${id}`, name: `f${id}`, kind: "function", parent_id: null, signature: null, line_start: id, line_end: id + 1, offset: id, end_offset: id + 2 });
it("bounds rendered structure and keeps candidate evidence distinct from containment", () => {
    const graph = { nodes: Array.from({ length: 1000 }, (_, id) => symbol(id)), edges: [{ id: 1, source_id: 0, target_id: 1, kind: "calls", confidence: "candidate", path: "src/auth.py", line_start: 1, line_end: 1 }], truncated: false } as GitGraph;
    const result = repositoryGraphModel(graph, "Source");
    expect(result.nodes.length).toBeLessThanOrEqual(100); expect(result.edges.length).toBeLessThanOrEqual(500); expect(result.bounded).toBe(true);
    expect(result.edges.find(edge => edge.id === "relation:1")?.style?.strokeDasharray).toBe("5 5");
    expect(result.edges.find(edge => edge.id.startsWith("contains:"))?.markerEnd).toBeUndefined();
});
