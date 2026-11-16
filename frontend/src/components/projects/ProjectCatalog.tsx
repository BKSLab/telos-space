import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { FolderKanban, Search, X } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import { PROJECT_STATUS_LABELS, type Dashboard, type ProjectStatus } from "@/lib/types";
import { ProjectCard } from "@/components/dashboard/ProjectCard";
import { PortfolioMetrics } from "@/components/dashboard/PortfolioMetrics";
import { Button, IconButton, LinkButton } from "@/components/ui/Button";
import { Input, Select } from "@/components/ui/Field";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";

export function ProjectCatalog() {
    const query = useQuery({ queryKey: queryKeys.dashboard, queryFn: () => api.get<Dashboard>(endpoints.dashboard()) });
    const [params, setParams] = useSearchParams();
    const search = params.get("q") ?? "";
    const requestedStatus = params.get("status");
    const status = requestedStatus && Object.hasOwn(PROJECT_STATUS_LABELS, requestedStatus) ? requestedStatus as ProjectStatus : "all";
    const needle = search.trim().toLocaleLowerCase();
    const projects = query.data?.projects ?? [];
    const filtered = projects.filter(project => (status === "all" || project.status === status)
        && `${project.name} ${project.key}`.toLocaleLowerCase().includes(needle));
    const hasFilters = needle.length > 0 || status !== "all";
    const updateFilter = (key: string, value: string) => setParams(current => {
        const next = new URLSearchParams(current);
        if (value && value !== "all") next.set(key, value); else next.delete(key);
        return next;
    }, { replace: true });
    const resetFilters = () => setParams(current => {
        const next = new URLSearchParams(current);
        next.delete("q");
        next.delete("status");
        return next;
    }, { replace: true });

    return <section aria-label="Каталог проектов" className="flex min-w-0 flex-col gap-4">
        {query.data && <PortfolioMetrics data={query.data} />}
        <div className="flex min-w-0 flex-col gap-3">
            <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-2">
                <h2 className="text-[16px] font-semibold tracking-[-0.02em] text-primary">Проекты портфеля</h2>
                {query.data && <p role="status" aria-label="Количество проектов" className="text-[12px] text-muted">{hasFilters ? `Найдено: ${filtered.length} из ${projects.length}` : `Всего проектов: ${projects.length}`}</p>}
            </div>
            <div className="flex min-w-0 flex-wrap items-center gap-3 rounded-[var(--radius-card)] border border-line-subtle bg-surface/50 p-3">
                <div className="relative min-w-0 basis-full sm:max-w-sm sm:flex-1 sm:basis-auto">
                    <Search size={16} aria-hidden="true" className="pointer-events-none absolute top-3 left-3 text-muted" />
                    <Input type="search" aria-label="Поиск проектов" placeholder="Название или ключ проекта" value={search}
                        onChange={event => updateFilter("q", event.target.value)} className="h-10 pr-10 pl-9" />
                    {search && <IconButton label="Очистить поиск проектов" size="sm" onClick={() => updateFilter("q", "")} className="absolute top-1 right-1"><X size={14} /></IconButton>}
                </div>
                <Select aria-label="Статус проекта" value={status} onChange={event => updateFilter("status", event.target.value)} className="h-10 w-auto min-w-40 flex-1 sm:flex-none">
                    <option value="all">Все статусы</option>
                    {(Object.entries(PROJECT_STATUS_LABELS) as [ProjectStatus, string][]).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </Select>
                {hasFilters && <Button size="sm" variant="ghost" onClick={resetFilters} className="text-[12px]">Сбросить</Button>}
            </div>
        </div>

        {query.isPending && <div role="status" aria-label="Загрузка каталога проектов" className="grid gap-3 md:grid-cols-2">{[0, 1, 2].map(index => <Skeleton key={index} className="h-48" />)}</div>}
        {query.error && <ErrorMessage message={(query.error as Error).message} action={<Button size="sm" onClick={() => void query.refetch()}>Повторить загрузку</Button>} />}
        {query.data && (projects.length === 0 ? <EmptyState title="Проектов пока нет" description="Создайте первый проект, чтобы вести задачи, структуру работ и документы."
            icon={<FolderKanban size={24} />} action={<LinkButton to="/projects/new" variant="primary">Создать проект</LinkButton>} />
            : filtered.length === 0 ? <EmptyState title="Проекты не найдены" description="Измените название или статус, чтобы увидеть другие проекты."
                icon={<Search size={24} />} action={<Button onClick={resetFilters}>Сбросить фильтры</Button>} />
                : <ul aria-label="Проекты портфеля" className="grid min-w-0 list-none gap-3 md:grid-cols-2">
                    {filtered.map(project => <li key={project.id} className="min-w-0"><ProjectCard project={project} /></li>)}
                </ul>)}
    </section>;
}
