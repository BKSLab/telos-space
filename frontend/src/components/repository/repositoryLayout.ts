import ELK from "elkjs/lib/elk-api.js";
import ElkWorker from "elkjs/lib/elk-worker.min.js?worker";
import RadialWorker from "./repositoryLayout.worker?worker";

export interface LayoutRequest { nodes: { id: string; label: string; width?: number; height?: number; cluster?: string; parentId?: string; group?: boolean }[]; edges: { id: string; source: string; target: string }[]; mode: string }
export interface LayoutPosition { id: string; x: number; y: number; width?: number; height?: number }

/** ELK's small promise adapter stays here; all layout computation runs in a local worker. */
export function layoutRepository(request: LayoutRequest, done: (positions: LayoutPosition[]) => void, failed: () => void) {
    let cancelled = false;
    if (request.mode === "radial" || request.mode === "layers") {
        const worker = new RadialWorker();
        worker.onmessage = ({ data }: MessageEvent<{ positions: LayoutPosition[] }>) => { if (!cancelled) done(data.positions); };
        worker.onerror = () => { if (!cancelled) failed(); };
        worker.postMessage(request);
        return () => { cancelled = true; worker.terminate(); };
    }
    const worker = new ElkWorker();
    worker.onerror = () => { if (!cancelled) failed(); };
    const elk = new ELK({ workerFactory: () => worker });
    void elk.layout({ id: "root", layoutOptions: {
        "elk.algorithm": "layered", "elk.direction": "DOWN",
        "elk.spacing.nodeNode": "30", "elk.layered.spacing.nodeNodeBetweenLayers": "64",
    }, children: request.nodes.map(node => ({ id: node.id, width: node.width ?? 240, height: node.height ?? 82 })),
    edges: request.edges.map(edge => ({ id: edge.id, sources: [edge.source], targets: [edge.target] })) }).then(result => {
        if (!cancelled) done(result.children?.map(node => ({ id: node.id, x: node.x ?? 0, y: node.y ?? 0 })) ?? []);
    }, () => { if (!cancelled) failed(); });
    return () => { cancelled = true; worker.terminate(); };
}
