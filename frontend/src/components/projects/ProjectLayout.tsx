import { useEffect, useRef, type CSSProperties } from "react";
import { NavLink, Outlet, useLocation, useMatch } from "react-router-dom";
import { Settings } from "lucide-react";
import { cn } from "@/lib/cn";
import { useProjectFromRoute } from "@/lib/useProject";
import { ProjectIcon } from "@/components/projects/ProjectIcon";
import { ProjectStatusBadge } from "@/components/ui/Badge";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";
import { ChatUnreadBadge } from "@/components/chat/ChatUnreadBadge";
import "./ProjectIdentity.css";

const TABS = [
    { path: "", label: "Обзор", end: true },
    { path: "tasks", label: "Задачи", end: false },
    { path: "chat", label: "Чат", end: false },
    { path: "team", label: "Команда", end: false },
    { path: "calendar", label: "Календарь", end: false },
    { path: "structure", label: "Структура", end: false },
    { path: "risks", label: "Риски", end: false },
    { path: "whiteboard", label: "Доска", end: false },
    { path: "docs", label: "Документы", end: false },
];

/**
 * Одна компактная шапка для всех разделов. В обзоре она входит в общую
 * прокрутку; рабочие холсты других вкладок сохраняют свой viewport.
 */
export function ProjectLayout() {
    const { project, projectKey, isPending, error, isMissing } = useProjectFromRoute();
    const location = useLocation();
    const isOverview = useMatch("/projects/:projectKey") !== null;
    const tabs = useRef<HTMLElement>(null);
    const workspace = useRef<HTMLDivElement>(null);
    useEffect(() => {
        if (workspace.current) workspace.current.scrollTop = 0;
        const navigation = tabs.current;
        const active = navigation?.querySelector<HTMLElement>('[aria-current="page"]');
        if (!navigation || !active) return;
        const revealActive = () => {
            const bounds = navigation.getBoundingClientRect();
            const tab = active.getBoundingClientRect();
            const offset = tab.left < bounds.left ? tab.left - bounds.left : Math.max(tab.right - bounds.right, 0);
            if (Math.abs(offset) > 0.5) navigation.scrollLeft += offset;
        };
        const frame = requestAnimationFrame(revealActive);
        const observer = new ResizeObserver(revealActive);
        observer.observe(navigation);
        navigation.querySelectorAll("a").forEach(tab => observer.observe(tab));
        return () => { cancelAnimationFrame(frame); observer.disconnect(); };
    }, [project?.id, location.pathname]);

    if (isPending) {
        return (
            <div className="flex flex-col gap-4 p-5">
                <Skeleton className="h-16 w-full max-w-2xl" />
                <Skeleton className="h-8 w-80" />
            </div>
        );
    }

    if (error) {
        return (
            <div className="p-5">
                <ErrorMessage message={(error as Error).message} />
            </div>
        );
    }

    if (isMissing || project === undefined) {
        return (
            <div className="p-5">
                <EmptyState
                    title={`Проект ${projectKey} не найден`}
                    description="Возможно, он был удалён или в адресе опечатка."
                />
            </div>
        );
    }

    return (
        <div ref={workspace} className={cn("project-workspace h-full min-w-0", isOverview ? "scrollbar-thin overflow-x-hidden overflow-y-auto" : "flex flex-col overflow-hidden")}>
            <header className="project-header project-header-compact material-metal shrink-0 border-b border-line bg-surface px-3 sm:px-5"
                style={{ "--project-color": project.color } as CSSProperties}>
                <div className="flex w-full min-w-0 flex-col">
                    <div className="project-header-details flex min-w-0 items-center justify-between gap-3 py-3">
                        <div className="flex min-w-0 flex-1 items-center gap-3">
                            <div className="project-header-logo"><ProjectIcon project={project} size={40} /></div>
                            <div className="flex min-w-0 flex-1 flex-col gap-1">
                                <div className="flex min-w-0 flex-col items-start gap-1">
                                    <h1 title={project.name} className="project-header-name max-w-full font-semibold tracking-[-0.025em] text-primary">
                                        {project.name}
                                    </h1>
                                    <div className="flex flex-wrap items-center gap-2">
                                        <span className="font-mono text-[12px] text-muted">
                                            {project.key}
                                        </span>
                                        <ProjectStatusBadge status={project.status} className="text-[12px]" />
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div className="flex items-center gap-3">
                            <NavLink aria-label="Настройки проекта" title="Настройки проекта" to={`/projects/${project.key}/settings`} className={({ isActive }) => cn("touch-target flex size-8 shrink-0 items-center justify-center rounded-md text-secondary hover:bg-hover", isActive && "bg-accent-soft text-accent")}>
                                <Settings size={17} aria-hidden="true" />
                            </NavLink>
                        </div>
                    </div>

                    <nav ref={tabs} aria-label="Разделы проекта" className="-mb-px flex w-full gap-0.5 overflow-x-auto overscroll-x-contain">
                        {TABS.map((tab) => (
                            <NavLink
                                key={tab.path}
                                to={`/projects/${project.key}${tab.path ? `/${tab.path}` : ""}`}
                                end={tab.end}
                                className={({ isActive }) =>
                                    cn(
                                        "brand-tab flex min-h-11 min-w-max shrink-0 items-center justify-center border-b px-4 py-2.5 text-[15px] whitespace-nowrap lg:flex-1",
                                        "transition-colors duration-[var(--duration-fast)]",
                                        isActive
                                            ? "border-accent bg-accent/[0.075] text-primary"
                                            : "border-transparent text-muted hover:bg-white/[0.035] hover:text-secondary",
                                    )
                                }
                            >
                                {tab.label}
                                {tab.path === "chat" && <ChatUnreadBadge />}
                            </NavLink>
                        ))}
                    </nav>
                </div>
            </header>

            <div className={isOverview ? "min-w-0" : "min-h-0 flex-1"}>
                <Outlet context={{ project }} />
            </div>

        </div>
    );
}
