import { useId, useState } from "react";
import { NavLink } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, FolderKanban, Plus, Search } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { Project } from "@/lib/types";
import { cn } from "@/lib/cn";
import { useUiStore } from "@/stores/ui";
import { ProjectIcon } from "@/components/projects/ProjectIcon";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { Skeleton } from "@/components/ui/States";

/** The project switcher is shared by the sidebar and the mobile menu. */
export function ProjectNavigation({ compact = false, mobile = false, onNavigate }: {
    compact?: boolean;
    mobile?: boolean;
    onNavigate?: () => void;
}) {
    const id = useId();
    const collapsed = useUiStore(state => state.projectListCollapsed);
    const toggle = useUiStore(state => state.toggleProjectList);
    const [search, setSearch] = useState("");
    const query = useQuery({ queryKey: queryKeys.projects, queryFn: () => api.get<Project[]>(endpoints.projects()) });
    const needle = compact ? "" : search.trim().toLocaleLowerCase();
    const projects = (query.data ?? []).filter(project => `${project.name} ${project.key}`.toLocaleLowerCase().includes(needle));
    const itemClass = (active: boolean) => cn(
        "brand-side-nav relative flex min-h-10 min-w-0 items-center gap-3 rounded-[var(--radius-control)] border border-transparent px-2 py-2 text-[13px]",
        "before:absolute before:top-2 before:bottom-2 before:left-0 before:w-0.5 before:rounded-full",
        "focus-visible:outline-2 focus-visible:outline-accent",
        mobile && "min-h-12 px-3 text-[14px]",
        compact && "justify-center px-0",
        active ? "border-line bg-white/[0.055] text-primary before:bg-accent" : "text-muted hover:bg-white/[0.035] hover:text-primary",
    );

    return <nav aria-label="Быстрый переход к проектам" className="min-w-0 border-t border-line-subtle pt-3">
        <button type="button" aria-expanded={!collapsed} aria-controls={id}
            aria-label={collapsed ? "Развернуть список проектов" : "Свернуть список проектов"}
            title={compact ? (collapsed ? "Развернуть список проектов" : "Свернуть список проектов") : undefined}
            onClick={toggle}
            className={cn("brand-projects-heading flex min-h-10 w-full items-center gap-2.5 rounded-md px-2 py-2 text-secondary hover:bg-hover focus-visible:outline-2 focus-visible:outline-accent", compact && "justify-center px-0", mobile && "min-h-12 px-3")}>
            <span aria-hidden="true" className="brand-projects-mark h-4 w-0.5 shrink-0 rounded-full" />
            {compact ? <FolderKanban size={18} aria-hidden="true" /> : <>
                <span className="text-[11px] font-semibold tracking-[0.08em] uppercase">Проекты</span>
                {query.data && <span className="text-[11px] text-muted tabular-nums">{query.data.length}</span>}
                <ChevronDown size={15} aria-hidden="true" className={cn("ml-auto shrink-0 transition-transform duration-[var(--duration-fast)]", collapsed && "-rotate-90")} />
            </>}
        </button>
        <div id={id} hidden={collapsed}>
            {!compact && (query.data?.length ?? 0) > 8 && <div className="relative my-2">
                <Search size={14} aria-hidden="true" className="pointer-events-none absolute top-2.5 left-2.5 text-muted" />
                <Input aria-label="Быстрый поиск проектов" placeholder="Название или ключ" value={search} onChange={event => setSearch(event.target.value)} className="pl-8" />
            </div>}
            {query.isPending && <div role="status" aria-label="Загрузка проектов" className="grid gap-2 py-2">{[0, 1, 2].map(index => <Skeleton key={index} className="h-10" />)}</div>}
            {query.error && !compact && <div role="alert" className="px-2 py-3 text-[12px] text-muted">
                Не удалось загрузить проекты.
                <Button size="sm" variant="ghost" onClick={() => void query.refetch()}>Повторить</Button>
            </div>}
            {!compact && query.data && projects.length === 0 && <p className="px-2 py-3 text-[12px] text-muted">{query.data.length ? "Ничего не найдено" : "Проектов пока нет"}</p>}
            {projects.map(project => <NavLink key={project.id} to={`/projects/${project.key}`} onClick={onNavigate}
                title={project.name} aria-label={compact ? project.name : undefined} className={({ isActive }) => itemClass(isActive)}>
                <ProjectIcon project={project} size={mobile ? 28 : 24} />
                {!compact && <span className="min-w-0 truncate">{project.name}</span>}
            </NavLink>)}
            <NavLink to="/projects/new" onClick={onNavigate} title={compact ? "Новый проект" : undefined}
                aria-label={compact ? "Новый проект" : undefined} className={({ isActive }) => itemClass(isActive)}>
                <Plus size={compact ? 20 : 18} aria-hidden="true" className="shrink-0" />
                {!compact && <span>Новый проект</span>}
            </NavLink>
        </div>
    </nav>;
}
