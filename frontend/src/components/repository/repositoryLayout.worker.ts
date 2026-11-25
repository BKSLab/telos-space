interface LayoutRequest { nodes: { id: string; label: string }[]; edges: { id: string; source: string; target: string }[]; mode: string }
self.onmessage = ({ data }: MessageEvent<LayoutRequest>) => {
    const count = Math.max(1, data.nodes.length - 1);
    const positions = data.nodes.map((node, index) => {
        if (!index) return { id: node.id, x: 600, y: 600 };
        const ring = Math.floor((index - 1) / 16) + 1;
        const angle = ((index - 1) % 16) * Math.PI * 2 / Math.min(16, count);
        return { id: node.id, x: 600 + Math.cos(angle) * ring * 330, y: 600 + Math.sin(angle) * ring * 330 };
    });
    self.postMessage({ positions });
};
