import { useEffect, useRef } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Settings } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { ProjectStats } from "@/lib/types";
import { cn } from "@/lib/cn";
import { useProjectFromRoute } from "@/lib/useProject";
import { ProjectIcon } from "@/components/projects/ProjectIcon";
import { ProjectStatusBadge } from "@/components/ui/Badge";
import { ProgressBar } from "@/components/ui/Progress";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";
import { ChatUnreadBadge } from "@/components/chat/ChatUnreadBadge";

const TABS = [
    { path: "", label: "Пульс", end: true },
    { path: "team", label: "Команда", end: false },
    { path: "board", label: "Канбан", end: false },
    { path: "tasks", label: "Задачи", end: false },
    { path: "calendar", label: "Календарь", end: false },
    { path: "structure", label: "Структура", end: false },
    { path: "risks", label: "Риски", end: false },
    { path: "whiteboard", label: "Доска", end: false },
    { path: "docs", label: "Документы", end: false },
    { path: "chat", label: "Чат", end: false },
];

/**
 * Рабочее пространство проекта: шапка с показателями и вкладки разделов
 * (раздел 5 дизайн-гайда). Содержимое вкладки рендерится через Outlet.
 */
export function ProjectLayout() {
    const { project, projectKey, isPending, error, isMissing } = useProjectFromRoute();
    const location = useLocation();
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

    return (
        <div className="flex h-full min-w-0 flex-col">
            <header className="project-header material-metal shrink-0 border-b border-line bg-surface px-3 pt-2 sm:px-5 sm:pt-4">
                <div className="flex w-full min-w-0 flex-col gap-3">
                    <div className="project-header-details flex items-start justify-between gap-2 sm:flex-wrap sm:gap-3">
                        <div className="flex min-w-0 flex-1 items-start gap-2.5">
                            <ProjectIcon project={project} size={32} />
                            <div className="flex min-w-0 flex-col gap-1">
                                <div className="flex flex-wrap items-center gap-2">
                                    <h1 title={project.name} className="max-w-full truncate text-[18px] font-semibold tracking-[-0.025em] text-primary sm:text-[24px]">
                                        {project.name}
                                    </h1>
                                    <span className="font-mono text-[12px] text-muted">
                                        {project.key}
                                    </span>
                                    <ProjectStatusBadge status={project.status} />
                                </div>
                                {statsQuery.data && (
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
                            <div className="hidden w-44 items-center gap-2 sm:flex">
                                <ProgressBar
                                    value={statsQuery.data?.completion_rate ?? 0}
                                    color={project.color}
                                    label={`Прогресс проекта ${project.name}`}
                                />
                                <span className="shrink-0 font-mono text-[12px] text-secondary">
                                    {percent}%
                                </span>
                            </div>
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
