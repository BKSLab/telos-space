import type { LayoutPosition, LayoutRequest } from "./repositoryLayout";

self.onmessage = ({ data }: MessageEvent<LayoutRequest>) => {
    const width = Math.max(240, ...data.nodes.filter(node => !node.group).map(node => node.width ?? 240));
    const height = Math.max(82, ...data.nodes.filter(node => !node.group).map(node => node.height ?? 82));
    const positions: LayoutPosition[] = [];
    if (data.mode === "layers") {
        let top = height + 80;
        positions.push({ id: "repo", x: width + 34, y: 0 });
        for (const group of data.nodes.filter(node => node.group)) {
            const members = data.nodes.filter(node => node.parentId === group.id);
            const columns = Math.min(3, members.length);
            const rows = Math.ceil(members.length / 3);
            positions.push({ id: group.id, x: 0, y: top, width: columns * (width + 24) + 8, height: rows * (height + 24) + 50 });
            members.forEach((node, index) => positions.push({ id: node.id, x: 16 + index % 3 * (width + 24), y: 54 + Math.floor(index / 3) * (height + 24) }));
            top += rows * (height + 24) + 86;
        }
        for (const node of data.nodes.filter(node => node.id !== "repo" && !node.group && !node.parentId)) {
            positions.push({ id: node.id, x: 16, y: top }); top += height + 24;
        }
    } else {
        const groups = new Map<string, typeof data.nodes>();
        for (const node of data.nodes.filter(node => node.id !== "repo")) {
            const key = node.cluster || node.id;
            if (!groups.has(key)) groups.set(key, []);
            groups.get(key)!.push(node);
        }
        // Each orbit represents an actual directory; members form a readable cluster.
        const clusters = [...groups.values()].map((members, index) => {
            const angle = -Math.PI / 2 + index * Math.PI * 2 / groups.size;
            const columns = Math.min(2, members.length);
            return { members, columns, dx: Math.cos(angle), dy: Math.sin(angle), width: columns * (width + 24), height: Math.ceil(members.length / columns) * (height + 24) };
        });
        let radius = 350;
        const separation = (dx: number, dy: number, horizontal: number, vertical: number) => Math.min(Math.abs(dx) > 0.001 ? horizontal / Math.abs(dx) : Infinity, Math.abs(dy) > 0.001 ? vertical / Math.abs(dy) : Infinity);
        for (const [index, cluster] of clusters.entries()) {
            radius = Math.max(radius, separation(cluster.dx, cluster.dy, (cluster.width + width) / 2 + 80, (cluster.height + height) / 2 + 80));
            for (const other of clusters.slice(0, index)) radius = Math.max(radius, separation(cluster.dx - other.dx, cluster.dy - other.dy, (cluster.width + other.width) / 2 + 60, (cluster.height + other.height) / 2 + 60));
        }
        positions.push({ id: "repo", x: -width / 2, y: -height / 2 });
        clusters.forEach(cluster => {
            const left = cluster.dx * radius - cluster.width / 2;
            const top = cluster.dy * radius - cluster.height / 2;
            cluster.members.forEach((node, index) => positions.push({ id: node.id, x: left + index % cluster.columns * (width + 24), y: top + Math.floor(index / cluster.columns) * (height + 24) }));
        });
    }
    self.postMessage({ positions });
};
