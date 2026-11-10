import { Link, useLocation } from "react-router-dom";
import { MessageSquare } from "lucide-react";
import { useProjectOutlet } from "@/lib/useProjectOutlet";
import { ProjectKnowledgePanel } from "@/components/knowledge/ProjectKnowledgePanel";

/** Самостоятельная рабочая область знаний без чтения личных диалогов. */
export function ProjectKnowledgePage() {
    const project = useProjectOutlet();
    const location = useLocation();
    return <section className="flex h-full min-h-0 flex-col" aria-label="Знания проекта">
        <header className="material-metal flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3">
            <h2 className="text-sm font-medium text-secondary">Знания проекта</h2>
            <Link className="flex min-h-9 items-center gap-2 rounded-md px-3 text-sm text-secondary hover:bg-hover" to={{ pathname: `/projects/${project.key}/chat`, search: location.search, hash: location.hash }}><MessageSquare size={14} aria-hidden="true" />Общий чат</Link>
        </header>
        <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto p-5">
            <div className="mx-auto flex w-full max-w-3xl flex-col gap-3"><ProjectKnowledgePanel projectId={project.id} /></div>
        </div>
    </section>;
}
