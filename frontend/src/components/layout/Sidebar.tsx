import { NavLink } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
    PanelLeftClose,
    PanelLeftOpen,
    Plus,
} from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { Project } from "@/lib/types";
import { cn } from "@/lib/cn";
import { useUiStore } from "@/stores/ui";
import { IconButton } from "@/components/ui/Button";
import { Tooltip } from "@/components/ui/Tooltip";
import { ProjectIcon } from "@/components/projects/ProjectIcon";
import { UserMenu } from "@/components/users/UserMenu";
import { useCurrentUser } from "@/lib/useAuth";
import { BrandLogo } from "@/components/brand/BrandLogo";
import { GLOBAL_NAV_ITEMS } from "@/lib/navigation";


function navLinkClass(isActive: boolean, collapsed: boolean): string {
    return cn(
        "brand-side-nav relative flex min-h-8 items-center gap-2.5 rounded-[var(--radius-control)] border border-transparent px-2 py-1.5 text-[13px]",
        "before:absolute before:top-1.5 before:bottom-1.5 before:left-0 before:w-0.5 before:rounded-full before:bg-transparent",
        "transition-[background-color,color] duration-[var(--duration-fast)] ease-[var(--ease-standard)]",
        collapsed && "justify-center px-0",
        isActive
            ? "border-line bg-white/[0.055] text-primary shadow-card before:bg-accent"
            : "text-muted hover:border-line-subtle hover:bg-white/[0.035] hover:text-secondary",
    );
}

export function Sidebar() {
    const collapsed = useUiStore((state) => state.sidebarCollapsed);
    const toggleSidebar = useUiStore((state) => state.toggleSidebar);

    const { data: user } = useCurrentUser();

    const projectsQuery = useQuery({
        queryKey: queryKeys.projects,
        queryFn: () => api.get<Project[]>(endpoints.projects()),
    });

    return (
        <aside
            className={cn(
                "app-sidebar material-metal hidden shrink-0 flex-col rounded-[var(--radius-panel)] border border-line lg:flex",
                "transition-[width] duration-[var(--duration-normal)] ease-[var(--ease-standard)]",
                collapsed ? "w-14" : "w-[232px]",
            )}
        >
            <div
                className={cn(
                    "flex shrink-0 items-center border-b border-line-subtle px-2",
                    collapsed ? "h-20 flex-col justify-center gap-1" : "h-14 justify-between gap-1",
                )}
            >
                <NavLink to="/" className="min-w-0 rounded focus-visible:outline-2 focus-visible:outline-accent">
                    <BrandLogo variant={collapsed ? "symbol" : "logo"} className={collapsed ? "size-7" : "w-[172px]"} />
                </NavLink>
                <Tooltip
                    content={collapsed ? "Развернуть панель" : "Свернуть панель"}
                    placement="right"
                >
                    <IconButton
                        label={collapsed ? "Развернуть панель" : "Свернуть панель"}
                        size="sm"
                        onClick={toggleSidebar}
                    >
                        {collapsed ? <PanelLeftOpen size={14} /> : <PanelLeftClose size={14} />}
                    </IconButton>
                </Tooltip>
            </div>

            <nav aria-label="Основная навигация" className="flex flex-col gap-0.5 p-2 pt-2.5">
                {GLOBAL_NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
                    <NavLink
                        key={to}
                        to={to}
                        end={end}
                        title={collapsed ? label : undefined}
                        className={({ isActive }) => navLinkClass(isActive, collapsed)}
                    >
                        <Icon size={16} aria-hidden="true" className="shrink-0" />
                        {!collapsed && <span className="truncate">{label}</span>}
                    </NavLink>
                ))}
            </nav>

            <div className="scrollbar-thin mt-1 flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-2 pb-2">
                {!collapsed && (
                    <p className="px-2 pt-2 pb-2 text-[10px] font-semibold tracking-[0.1em] text-disabled uppercase">
                        Проекты
                    </p>
                )}
                {projectsQuery.data?.map((project) => (
                    <NavLink
                        key={project.id}
                        to={`/projects/${project.key}`}
                        title={collapsed ? project.name : undefined}
                        className={({ isActive }) => navLinkClass(isActive, collapsed)}
                    >
                        <ProjectIcon project={project} size={16} />
                        {!collapsed && <span className="truncate">{project.name}</span>}
                    </NavLink>
                ))}
                <NavLink
                    to="/projects/new"
                    title={collapsed ? "Новый проект" : undefined}
                    className={({ isActive }) => navLinkClass(isActive, collapsed)}
                >
                    <Plus size={16} aria-hidden="true" className="shrink-0" />
                    {!collapsed && <span className="truncate">Новый проект</span>}
                </NavLink>
            </div>

            {user && <UserMenu user={user} collapsed={collapsed} />}
        </aside>
    );
}
