import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, FolderKanban, Search, X } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import { PROJECT_STATUS_LABELS, type Dashboard, type ProjectStatus } from "@/lib/types";
import { ProjectCard } from "@/components/dashboard/ProjectCard";
import { ProjectIcon } from "@/components/projects/ProjectIcon";
import { ProjectStatusBadge } from "@/components/ui/Badge";
import { Button, IconButton, LinkButton } from "@/components/ui/Button";
import { DueDate } from "@/components/ui/DueDate";
import { Input, Select } from "@/components/ui/Field";
import { ProgressBar } from "@/components/ui/Progress";
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
        <div className="flex flex-wrap items-center gap-3">
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
            {query.data && <p role="status" aria-label="Количество проектов" className="text-[12px] text-muted sm:ml-auto">{hasFilters ? `Найдено: ${filtered.length} из ${projects.length}` : `Всего проектов: ${projects.length}`}</p>}
        </div>

        {query.isPending && <div role="status" aria-label="Загрузка каталога проектов" className="grid gap-3">{[0, 1, 2].map(index => <Skeleton key={index} className="h-24" />)}</div>}
        {query.error && <ErrorMessage message={(query.error as Error).message} action={<Button size="sm" onClick={() => void query.refetch()}>Повторить загрузку</Button>} />}
        {query.data && (projects.length === 0 ? <EmptyState title="Проектов пока нет" description="Создайте первый проект, чтобы вести задачи, структуру работ и документы."
            icon={<FolderKanban size={24} />} action={<LinkButton to="/projects/new" variant="primary">Создать проект</LinkButton>} />
            : filtered.length === 0 ? <EmptyState title="Проекты не найдены" description="Измените название или статус, чтобы увидеть другие проекты."
                icon={<Search size={24} />} action={<Button onClick={resetFilters}>Сбросить фильтры</Button>} /> : <>
                <div className="hidden overflow-hidden rounded-[var(--radius-card)] border border-line-subtle bg-surface/35 md:block">
                    <table className="w-full table-fixed text-left text-[13px]">
                        <caption className="sr-only">Проекты портфеля</caption>
                        <thead className="border-b border-line-subtle bg-white/[0.02] text-[12px] text-muted">
                            <tr>
                                <th scope="col" className="w-[30%] px-4 py-3 font-medium">Проект</th>
                                <th scope="col" className="w-[19%] px-3 py-3 font-medium">Статус</th>
                                <th scope="col" className="w-[20%] px-3 py-3 font-medium">Прогресс</th>
                                <th scope="col" className="w-[14%] px-3 py-3 font-medium">Просрочено</th>
                                <th scope="col" className="w-[17%] px-3 py-3 font-medium">Ближайший срок</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-line-subtle">
                            {filtered.map(project => <tr key={project.id} className="hover:bg-white/[0.025] focus-within:bg-white/[0.035]">
                                <td className="px-4 py-4">
                                    <Link to={`/projects/${project.key}`} className="flex min-h-11 min-w-0 items-center gap-3 rounded-md focus-visible:outline-2 focus-visible:outline-accent">
                                        <ProjectIcon project={project} size={36} />
                                        <span className="flex min-w-0 flex-col gap-1">
                                            <span title={project.name} className="truncate text-[14px] font-semibold text-primary hover:text-accent">{project.name}</span>
                                            <span className="font-mono text-[11px] text-muted">{project.key}</span>
                                        </span>
                                    </Link>
                                </td>
                                <td className="px-3 py-4"><ProjectStatusBadge status={project.status} className="max-w-full text-[12px]" /></td>
                                <td className="px-3 py-4">
                                    <div className="flex items-baseline justify-between gap-2 pb-2">
                                        <span className="text-[11px] text-muted">{project.done_tasks} из {project.total_tasks}</span>
                                        <span className="font-mono text-[12px] text-primary">{project.total_tasks ? `${Math.round(project.completion_rate * 100)}%` : "—"}</span>
                                    </div>
                                    <ProgressBar value={project.completion_rate} color={project.color} label={`Прогресс проекта ${project.name}`} />
                                </td>
                                <td className="px-3 py-4">{project.overdue_tasks > 0 ? <span className="inline-flex items-center gap-1.5 text-danger"><AlertTriangle size={14} aria-hidden="true" />{project.overdue_tasks}</span> : <span className="text-muted">—</span>}</td>
                                <td className="px-3 py-4">{project.next_due_date ? <DueDate value={project.next_due_date} className="text-[12px]" /> : <span className="text-muted">—</span>}</td>
                            </tr>)}
                        </tbody>
                    </table>
                </div>
                <div className="grid gap-3 md:hidden">{filtered.map(project => <ProjectCard key={project.id} project={project} />)}</div>
            </>)}
    </section>;
}
