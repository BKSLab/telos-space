import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { ChevronDown, ChevronRight, Code2, FileCode2, Folder, GitBranch } from "lucide-react";
import type { GraphNodeData } from "./repositoryGraphModel";

export function RepositoryGraphNode({ data, selected }: NodeProps<Node<GraphNodeData>>) {
    const Icon = data.kind === "repository" ? GitBranch : data.kind === "directory" ? Folder : data.kind === "file" ? FileCode2 : Code2;
    return <div className={`repository-graph-card repository-graph-card-${data.kind}${selected ? " is-selected" : ""}`} title={`${data.path}\n${data.detail}`}>
        <Handle type="target" position={Position.Top} />
        <span className="repository-graph-card-icon"><Icon size={23} strokeWidth={1.6} /></span>
        <div className="repository-graph-card-copy"><strong>{data.label}</strong><span className="repository-graph-card-path">{data.path}</span><span className="repository-graph-card-detail">{data.detail}</span></div>
        {data.kind === "file" && data.onExpand && <button type="button" className="repository-graph-disclosure nodrag nopan" aria-expanded={data.expanded} aria-label={`${data.expanded ? "Свернуть" : "Раскрыть"} символы ${data.label}`} onClick={event => { event.stopPropagation(); data.onExpand?.(); }}>{data.expanded ? <ChevronDown size={17} /> : <ChevronRight size={17} />}</button>}
        <Handle type="source" position={Position.Bottom} />
    </div>;
}
export function RepositoryGraphGroup({ data }: NodeProps<Node<GraphNodeData>>) {
    return <div className="repository-graph-group"><div className="repository-graph-group-heading"><Folder size={17} /><strong>{data.label}</strong><span>Каталог</span></div></div>;
}
