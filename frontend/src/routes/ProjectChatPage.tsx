import { lazy, Suspense } from "react";
import { Navigate, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { MessageSquare } from "lucide-react";
import { ProjectChatWorkspace } from "@/components/chat/ProjectChatWorkspace";
import { useProjectOutlet } from "@/lib/useProjectOutlet";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/States";

const ProjectAgentWorkspace = lazy(() => import("@/components/agent/ProjectAgentWorkspace").then(module => ({ default: module.ProjectAgentWorkspace })));

export function ProjectChatPage() {
    const project = useProjectOutlet();
    const location = useLocation();
    const navigate = useNavigate();
    const [params] = useSearchParams();
    const view = params.get("view");
    const legacy = view === "personal" || view === "knowledge";
    if (view === "vera") {
        const next = new URLSearchParams(params);
        next.delete("view");
        return <Navigate replace to={{ pathname: "/chat", search: next.toString(), hash: location.hash }} />;
    }
    const showProjectChat = () => {
        const next = new URLSearchParams(params);
        next.delete("view");
        navigate({ pathname: location.pathname, search: next.toString(), hash: location.hash });
    };
    return <section className="flex h-full min-h-0 flex-col" aria-label="Чат проекта">
        {legacy && <header className="material-metal flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3">
            <h2 className="text-sm font-medium text-secondary">{view === "knowledge" ? "Знания проекта" : "Личные диалоги проекта · видно только вам"}</h2>
            <Button size="sm" variant="ghost" icon={<MessageSquare size={14} />} onClick={showProjectChat}>Общий чат</Button>
        </header>}
        <div className="min-h-0 flex-1">
            {legacy ? <Suspense fallback={<div className="p-5"><Skeleton className="h-48 w-full" /></div>}>
                <ProjectAgentWorkspace key={project.id} project={project} knowledgeOnly={view === "knowledge"} />
            </Suspense> : <ProjectChatWorkspace />}
        </div>
    </section>;
}
