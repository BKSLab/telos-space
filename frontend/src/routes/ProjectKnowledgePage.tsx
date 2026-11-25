import { lazy, Suspense, useLayoutEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { BookOpen, GitBranch, History } from "lucide-react";
import { useProjectOutlet } from "@/lib/useProjectOutlet";
import { CollapsibleSection } from "@/components/ui/Card";
import { ProjectKnowledgePanel as IndexDiagnostics } from "@/components/knowledge/ProjectKnowledgePanel";
import { ProjectKnowledgePanel } from "@/components/projects/ProjectKnowledgePanel";
import { ProjectHistoryTimeline } from "@/components/projects/ProjectHistoryTimeline";
const RepositoryWorkspace = lazy(() => import("@/components/repository/RepositoryWorkspace").then(module => ({ default: module.RepositoryWorkspace })));

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
    const [codeToolbar, setCodeToolbar] = useState<HTMLDivElement | null>(null);
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
    return <section ref={scroll} className={`scrollbar-thin h-full min-h-0 ${view === "code" ? "flex flex-col overflow-hidden" : "overflow-y-auto"}`} aria-label="Знания проекта">
        <div className={`mx-auto flex w-full flex-col gap-4 px-3 py-3 sm:px-5 sm:py-4 ${view === "code" ? "min-h-0 flex-1" : "max-w-[1440px]"}`}>
            <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-b border-line-subtle">
                <h2 className="text-lg font-semibold tracking-tight text-primary">Знания</h2>
                <div className="flex w-full min-w-0 items-center gap-2 sm:w-auto"><nav aria-label="Виды знаний" className="flex min-w-0 flex-1 gap-1">
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
                            <Icon size={15} aria-hidden="true" className={view === "code" ? "hidden sm:block" : undefined} />{label}
                        </Link>;
                    })}
                </nav>{view === "code" && <div ref={setCodeToolbar} />}</div>
            </header>
            {view === "history" ? <ProjectHistoryTimeline key={project.id} projectId={project.id} projectKey={project.key} />
                : view === "map" ? <>
                        <ProjectKnowledgePanel key={project.id} projectId={project.id} projectKey={project.key} ownerId={project.owner_id ?? null} />
                        <CollapsibleSection title="Диагностика индекса"><IndexDiagnostics projectId={project.id} /></CollapsibleSection>
                    </> : null}
            {view === "code" && <Suspense fallback={<p role="status">Загружаем код проекта…</p>}><RepositoryWorkspace key={project.id} projectId={project.id} toolbarTarget={codeToolbar} /></Suspense>}
        </div>
    </section>;
}
