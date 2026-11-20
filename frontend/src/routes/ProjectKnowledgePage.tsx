import { Link, useLocation } from "react-router-dom";
import { BookOpen, History, MessageSquare } from "lucide-react";
import { useProjectOutlet } from "@/lib/useProjectOutlet";
import { cn } from "@/lib/cn";
import { CollapsibleSection } from "@/components/ui/Card";
import { ProjectKnowledgePanel as IndexDiagnostics } from "@/components/knowledge/ProjectKnowledgePanel";
import { ProjectKnowledgePanel } from "@/components/projects/ProjectKnowledgePanel";
import { ProjectHistoryTimeline } from "@/components/projects/ProjectHistoryTimeline";

/** Самостоятельная рабочая область знаний без чтения личных диалогов. */
export function ProjectKnowledgePage() {
    const project = useProjectOutlet();
    const location = useLocation();
    const params = new URLSearchParams(location.search);
    const history = params.get("view") === "history";
    params.delete("view");
    const mapSearch = params.toString();
    const chatParams = new URLSearchParams(params);
    for (const key of ["q", "kind", "actor", "since", "until", "offset"]) chatParams.delete(key);
    const chatSearch = chatParams.toString();
    const historySearch = new URLSearchParams(params);
    historySearch.set("view", "history");
    return <section className="scrollbar-thin h-full min-h-0 overflow-y-auto" aria-label="Знания проекта">
        <div className="mx-auto flex w-full max-w-[1440px] flex-col gap-5 px-3 py-4 sm:px-5 sm:py-5">
            <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-line-subtle pb-3">
                <h2 className="text-lg font-semibold tracking-tight text-primary">Знания проекта</h2>
                <nav aria-label="Виды знаний" className="order-last flex w-full gap-1 rounded-[var(--radius-control)] border border-line bg-surface p-1 sm:order-none sm:ml-auto sm:w-auto">
                    {[
                        { label: "Карта", icon: BookOpen, active: !history, search: mapSearch },
                        { label: "История", icon: History, active: history, search: historySearch.toString() },
                    ].map(({ label, icon: Icon, active, search }) => <Link key={label}
                        aria-current={active ? "page" : undefined} to={{ search, hash: location.hash }}
                        className={cn("touch-target flex flex-1 items-center justify-center gap-2 rounded-[var(--radius-control)] px-3 py-1.5 text-[13px] font-medium", active ? "bg-accent-soft text-accent" : "text-secondary hover:bg-hover")}
                    ><Icon size={15} aria-hidden="true" />{label}</Link>)}
                </nav>
                <Link className="touch-target flex min-h-9 items-center gap-2 rounded-md px-3 text-sm text-secondary hover:bg-hover" to={{ pathname: `/projects/${project.key}/chat`, search: chatSearch, hash: location.hash }}><MessageSquare size={14} aria-hidden="true" />Общий чат</Link>
            </header>
            {history ? <ProjectHistoryTimeline key={project.id} projectId={project.id} projectKey={project.key} />
                : <>
                    <ProjectKnowledgePanel key={project.id} projectId={project.id} projectKey={project.key} ownerId={project.owner_id ?? null} />
                    <CollapsibleSection title="Диагностика индекса"><IndexDiagnostics projectId={project.id} /></CollapsibleSection>
                </>}
        </div>
    </section>;
}
