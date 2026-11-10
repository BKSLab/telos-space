import { Link } from "react-router-dom";
import type { MyTaskContext } from "@/lib/types";
import { ProjectIcon } from "@/components/projects/ProjectIcon";

export function TaskSourceLabel({ context }: { context: MyTaskContext | null }) {
    if (!context) return <span className="text-xs text-muted">Разовая</span>;
    return <Link to={`/projects/${context.key}`} title={context.name} onClick={event => event.stopPropagation()} className="inline-flex min-w-0 max-w-full items-center gap-1.5 text-xs text-secondary">
        <ProjectIcon project={{ id: context.id, color: context.color ?? "#58a6ff", icon: context.icon ?? null, icon_image_version: context.icon_image_version }} size={14} />
        <span className="shrink-0 font-mono text-muted">{context.key}</span><span className="truncate">{context.name}</span>
    </Link>;
}
