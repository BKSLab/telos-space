import { afterEach, expect, it, vi } from "vitest";
import type { LayoutPosition, LayoutRequest } from "./repositoryLayout";

afterEach(() => vi.unstubAllGlobals());
it("keeps cards in real radial directory clusters without overlapping tall neighboring clusters", async () => {
    let result: LayoutPosition[] = [];
    const scope = { onmessage: undefined as ((event: MessageEvent<LayoutRequest>) => void) | undefined, postMessage: (data: { positions: LayoutPosition[] }) => { result = data.positions; } };
    vi.stubGlobal("self", scope);
    await import("./repositoryLayout.worker");
    const nodes = [{ id: "repo", label: "Source", cluster: "" }, ...Array.from({ length: 8 }, (_, directory) => Array.from({ length: 10 }, (_, file) => ({ id: `${directory}:${file}`, label: `file-${file}.ts`, cluster: `src/directory-${directory}` }))).flat()];
    scope.onmessage!(new MessageEvent<LayoutRequest>("message", { data: { nodes, edges: [], mode: "radial" } }));
    expect(result).toHaveLength(nodes.length);
    for (const [index, node] of result.entries()) {
        expect(Number.isFinite(node.x) && Number.isFinite(node.y)).toBe(true);
        for (const other of result.slice(0, index)) {
            const overlaps = node.x < other.x + 240 && node.x + 240 > other.x && node.y < other.y + 82 && node.y + 82 > other.y;
            expect(overlaps, `${node.id} overlaps ${other.id}`).toBe(false);
        }
    }
    scope.onmessage!(new MessageEvent<LayoutRequest>("message", { data: { nodes: [{ id: "repo", label: "Source" }, { id: "group:src", label: "src", group: true }, ...nodes.slice(1, 8).map(node => ({ ...node, parentId: "group:src" }))], edges: [], mode: "layers" } }));
    const group = result.find(node => node.id === "group:src")!;
    for (const member of result.filter(node => node.id !== "repo" && node.id !== group.id)) {
        expect(member.x).toBeGreaterThanOrEqual(0);
        expect(member.y).toBeGreaterThan(42);
        expect(member.x + 240).toBeLessThanOrEqual(group.width!);
        expect(member.y + 82).toBeLessThanOrEqual(group.height!);
    }
});
