import { lazy, Suspense } from "react";
import { Link, useLocation } from "react-router-dom";
import { MessageSquare } from "lucide-react";
import { useProjectOutlet } from "@/lib/useProjectOutlet";
import { Skeleton } from "@/components/ui/States";

const ProjectConversationWorkspace = lazy(() => import("@/components/agent/ProjectConversationWorkspace").then(module => ({ default: module.ProjectConversationWorkspace })));

/** Приватная история Ариса в контексте одного проекта. */
export function ProjectConversationsPage() {
    const project = useProjectOutlet();
    const location = useLocation();
    return <section className="flex h-full min-h-0 flex-col" aria-label="Личные диалоги проекта">
        <header className="material-metal flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3">
            <h2 className="text-sm font-medium text-secondary">Личные диалоги проекта · видно только вам</h2>
            <Link className="flex min-h-9 items-center gap-2 rounded-md px-3 text-sm text-secondary hover:bg-hover" to={{ pathname: `/projects/${project.key}/chat`, search: location.search, hash: location.hash }}><MessageSquare size={14} aria-hidden="true" />Общий чат</Link>
        </header>
        <div className="min-h-0 flex-1"><Suspense fallback={<div className="p-5"><Skeleton className="h-48 w-full" /></div>}>
            <ProjectConversationWorkspace key={project.id} project={project} />
        </Suspense></div>
    </section>;
}
