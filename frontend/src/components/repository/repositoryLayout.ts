import ELK from "elkjs/lib/elk-api.js";
import ElkWorker from "elkjs/lib/elk-worker.min.js?worker";
import RadialWorker from "./repositoryLayout.worker?worker";

interface LayoutRequest { nodes: { id: string; label: string }[]; edges: { id: string; source: string; target: string }[]; mode: string }
interface Position { id: string; x: number; y: number }

/** ELK's small promise adapter stays here; all layout computation runs in a local worker. */
export function layoutRepository(request: LayoutRequest, done: (positions: Position[]) => void, failed: () => void) {
    let cancelled = false;
    if (request.mode === "radial") {
        const worker = new RadialWorker();
        worker.onmessage = ({ data }: MessageEvent<{ positions: Position[] }>) => { if (!cancelled) done(data.positions); };
        worker.onerror = () => { if (!cancelled) failed(); };
        worker.postMessage(request);
        return () => { cancelled = true; worker.terminate(); };
    }
    const worker = new ElkWorker();
    worker.onerror = () => { if (!cancelled) failed(); };
    const elk = new ELK({ workerFactory: () => worker });
    void elk.layout({ id: "root", layoutOptions: {
        "elk.algorithm": "layered", "elk.direction": request.mode === "layers" ? "RIGHT" : "DOWN",
        "elk.spacing.nodeNode": "36", "elk.layered.spacing.nodeNodeBetweenLayers": "70",
    }, children: request.nodes.map(node => ({ id: node.id, width: 210, height: 64 })),
    edges: request.edges.map(edge => ({ id: edge.id, sources: [edge.source], targets: [edge.target] })) }).then(result => {
        if (!cancelled) done(result.children?.map(node => ({ id: node.id, x: node.x ?? 0, y: node.y ?? 0 })) ?? []);
    }, () => { if (!cancelled) failed(); });
    return () => { cancelled = true; worker.terminate(); };
}
