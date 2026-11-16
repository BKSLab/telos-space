import { useEffect, useMemo, useRef, type CSSProperties } from "react";
import { NavLink, Outlet, useLocation, useMatch } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Settings } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { ProjectStats } from "@/lib/types";
import { cn } from "@/lib/cn";
import { useProjectFromRoute } from "@/lib/useProject";
import { useRenderedMarkdown } from "@/lib/useRenderedMarkdown";
import { ProjectIcon } from "@/components/projects/ProjectIcon";
import { ProjectStatusBadge } from "@/components/ui/Badge";
import { ProgressBar } from "@/components/ui/Progress";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";
import { ChatUnreadBadge } from "@/components/chat/ChatUnreadBadge";
import "./ProjectIdentity.css";

const TABS = [
    { path: "", label: "Обзор", end: true },
    { path: "chat", label: "Чат", end: false },
    { path: "team", label: "Команда", end: false },
    { path: "tasks", label: "Задачи", end: false },
    { path: "calendar", label: "Календарь", end: false },
    { path: "structure", label: "Структура", end: false },
    { path: "risks", label: "Риски", end: false },
    { path: "whiteboard", label: "Доска", end: false },
    { path: "docs", label: "Документы", end: false },
];

function ProjectDescriptionPreview({ markdown }: { markdown: string }) {
    const html = useRenderedMarkdown(markdown);
    const description = useMemo(() => {
        const document = new DOMParser().parseFromString(html, "text/html");
        document.querySelectorAll("p, li, h1, h2, h3, h4, h5, h6, br, pre").forEach(element => element.append(" "));
        return (document.body.textContent ?? "").replace(/\s+/g, " ").trim();
    }, [html]);
    if (!description) return null;
    const excerpt = description.length > 240 ? `${description.slice(0, 237).trimEnd()}…` : description;
    return <p className="project-overview-description text-[14px] leading-relaxed text-secondary" title={description}>{excerpt}</p>;
}

/**
 * Рабочее пространство проекта: шапка с показателями и вкладки разделов
 * (раздел 5 дизайн-гайда). Содержимое вкладки рендерится через Outlet.
 */
export function ProjectLayout() {
    const { project, projectKey, isPending, error, isMissing } = useProjectFromRoute();
    const location = useLocation();
    const isOverview = useMatch("/projects/:projectKey") !== null;
    const tabs = useRef<HTMLElement>(null);
    useEffect(() => {
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

    const statsQuery = useQuery({
        queryKey: queryKeys.projectStats(project?.id ?? 0),
        queryFn: () => api.get<ProjectStats>(endpoints.projectStats(project?.id as number)),
        enabled: project !== undefined,
    });

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

    const percent = Math.round((statsQuery.data?.completion_rate ?? 0) * 100);
    const description = project.description_sections?.goal?.trim() || project.description_md || "";

    return (
        <div className="flex h-full min-w-0 flex-col">
            <header className={cn("project-header material-metal shrink-0 border-b border-line bg-surface px-3 pt-2 sm:px-5 sm:pt-4", isOverview && "project-overview-header")}
                style={{ "--project-color": project.color } as CSSProperties}>
                <div className="flex w-full min-w-0 flex-col gap-3">
                    <div className="project-header-details flex items-start justify-between gap-2 sm:flex-wrap sm:gap-3"
                        role={isOverview ? "group" : undefined} aria-label={isOverview ? "Сведения о проекте" : undefined} tabIndex={isOverview ? 0 : undefined}>
                        <div className={cn("flex min-w-0 flex-1 items-start gap-2.5", isOverview && "project-overview-identity")}>
                            {isOverview ? <div className="project-overview-logo"><ProjectIcon project={project} size={72} /></div>
                                : <ProjectIcon project={project} size={32} />}
                            <div className={cn("flex min-w-0 flex-1 flex-col gap-1", isOverview && "gap-2")}>
                                <div className={cn("flex flex-wrap items-center gap-2", isOverview && "flex-col items-start")}>
                                    <h1 title={project.name} className={cn("max-w-full font-semibold tracking-[-0.025em] text-primary", isOverview ? "project-overview-name" : "truncate text-[18px] sm:text-[24px]")}>
                                        {project.name}
                                    </h1>
                                    <div className={cn("flex flex-wrap items-center gap-2", !isOverview && "contents")}>
                                        <span className="font-mono text-[12px] text-muted">
                                            {project.key}
                                        </span>
                                        <ProjectStatusBadge status={project.status} className={isOverview ? "text-[12px]" : undefined} />
                                    </div>
                                </div>
                                {isOverview && description && <ProjectDescriptionPreview key={`${project.id}:${description}`} markdown={description} />}
                                {!isOverview && statsQuery.data && (
                                    <p className="text-[12px] text-muted sm:text-[13px]">
                                        {statsQuery.data.total_tasks} задач ·{" "}
                                        {statsQuery.data.in_progress_tasks} в работе ·{" "}
                                        {statsQuery.data.done_tasks} готово
                                        {statsQuery.data.overdue_tasks > 0 && (
                                            <span className="ml-1 text-danger">
                                                · {statsQuery.data.overdue_tasks} просрочено
                                            </span>
                                        )}
                                    </p>
                                )}
                            </div>
                        </div>

                        <div className="flex items-center gap-3">
                            {!isOverview && <div className="hidden w-44 items-center gap-2 sm:flex">
                                <ProgressBar
                                    value={statsQuery.data?.completion_rate ?? 0}
                                    color={project.color}
                                    label={`Прогресс проекта ${project.name}`}
                                />
                                <span className="shrink-0 font-mono text-[12px] text-secondary">
                                    {percent}%
                                </span>
                            </div>}
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

            <div className="min-h-0 flex-1">
                <Outlet context={{ project }} />
            </div>

        </div>
    );
}
