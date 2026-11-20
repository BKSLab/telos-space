import { Activity, useLayoutEffect, useRef } from "react";
import { Link, useLocation } from "react-router-dom";
import { BookOpen, GitBranch, History } from "lucide-react";
import { useProjectOutlet } from "@/lib/useProjectOutlet";
import { CollapsibleSection } from "@/components/ui/Card";
import { ProjectKnowledgePanel as IndexDiagnostics } from "@/components/knowledge/ProjectKnowledgePanel";
import { ProjectKnowledgePanel } from "@/components/projects/ProjectKnowledgePanel";
import { ProjectHistoryTimeline } from "@/components/projects/ProjectHistoryTimeline";
import { ProjectGitSources } from "@/components/projects/ProjectGitSources";

const positions = new Map<string, number>();

/** Each mode retains its filters; source selection does not reset the reading position. */
export function ProjectKnowledgePage() {
    const project = useProjectOutlet();
    const location = useLocation();
    const params = new URLSearchParams(location.search);
    const view = params.get("view") === "history" ? "history" : params.get("view") === "code" ? "code" : "map";
    const scrollParams = new URLSearchParams();
    const fields = view === "map" ? ["mq", "category", "map_offset"] : view === "history" ? ["q", "kind", "actor", "actor_user_id", "since", "until", "task_id", "timezone", "event"] : [];
    for (const field of fields) if (params.has(field)) scrollParams.set(field, params.get(field)!);
    const positionKey = `${project.id}:${view}:${scrollParams}`;
    const scroll = useRef<HTMLElement>(null);
    useLayoutEffect(() => {
        const element = scroll.current;
        if (!element) return;
        const target = positions.get(positionKey) ?? 0;
        let restoring = true;
        const restore = () => {
            if (!restoring) return;
            element.scrollTop = target;
            if (element.scrollHeight - element.clientHeight >= target) restoring = false;
        };
        restore();
        const observer = new ResizeObserver(restore);
        if (element.firstElementChild) observer.observe(element.firstElementChild);
        const stopRestoring = () => { restoring = false; };
        const save = () => { if (!restoring) positions.set(positionKey, element.scrollTop); };
        element.addEventListener("scroll", save);
        element.addEventListener("wheel", stopRestoring, { passive: true });
        element.addEventListener("touchstart", stopRestoring, { passive: true });
        element.addEventListener("keydown", stopRestoring);
        return () => {
            observer.disconnect();
            element.removeEventListener("scroll", save);
            element.removeEventListener("wheel", stopRestoring);
            element.removeEventListener("touchstart", stopRestoring);
            element.removeEventListener("keydown", stopRestoring);
            if (positions.size > 80) positions.delete(positions.keys().next().value!);
        };
    }, [positionKey]);
    return <section ref={scroll} className="scrollbar-thin h-full min-h-0 overflow-y-auto" aria-label="Знания проекта">
        <div className="mx-auto flex w-full max-w-[1440px] flex-col gap-4 px-3 py-3 sm:px-5 sm:py-4">
            <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-b border-line-subtle">
                <h2 className="text-lg font-semibold tracking-tight text-primary">Знания</h2>
                <nav aria-label="Виды знаний" className="flex w-full gap-1 sm:w-auto">
                    {[
                        { label: "Карта", icon: BookOpen, value: "map" },
                        { label: "История", icon: History, value: "history" },
                        { label: "Код", icon: GitBranch, value: "code" },
                    ].map(({ label, icon: Icon, value }) => {
                        const next = new URLSearchParams(params);
                        if (value === "map") next.delete("view"); else next.set("view", value);
                        return <Link key={value} aria-label={value === "code" ? "Код и репозитории" : label}
                            aria-current={view === value ? "page" : undefined} to={{ search: next.toString() }}
                            className="brand-tab touch-target flex flex-1 items-center justify-center gap-2 px-3 py-2 text-[13px] font-medium text-secondary aria-[current=page]:text-primary">
                            <Icon size={15} aria-hidden="true" />{label}
                        </Link>;
                    })}
                </nav>
            </header>
            {view === "history" ? <ProjectHistoryTimeline key={project.id} projectId={project.id} projectKey={project.key} />
                : view === "map" ? <>
                        <ProjectKnowledgePanel key={project.id} projectId={project.id} projectKey={project.key} ownerId={project.owner_id ?? null} />
                        <CollapsibleSection title="Диагностика индекса"><IndexDiagnostics projectId={project.id} /></CollapsibleSection>
                    </> : null}
            <Activity mode={view === "code" ? "visible" : "hidden"}>
                <div className="min-w-0"><h3 className="mb-3 text-sm font-semibold text-primary">Код и репозитории</h3><ProjectGitSources key={project.id} projectId={project.id} canEdit={false} /></div>
            </Activity>
        </div>
    </section>;
}
