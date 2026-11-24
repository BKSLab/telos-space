import { useDeferredValue, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Columns3, List, ListTodo, Plus, Search } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { ProjectStage, TaskListItem } from "@/lib/types";
import { PRIORITY_LABELS, PRIORITY_ORDER } from "@/lib/types";
import { cn } from "@/lib/cn";
import { useProjectOutlet } from "@/lib/useProjectOutlet";
import { useUiStore } from "@/stores/ui";
import { Button } from "@/components/ui/Button";
import { Input, Select } from "@/components/ui/Field";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";
import { Board } from "@/components/kanban/Board";
import { CreateTaskDialog } from "@/components/tasks/CreateTaskDialog";
import { TasksTable } from "@/components/tasks/TasksTable";

const VIEWS = [
    { id: "kanban", label: "Канбан", icon: Columns3 },
    { id: "list", label: "Список", icon: List },
] as const;

function TasksSkeleton({ list }: { list: boolean }) {
    return <div role="status" aria-label="Загрузка задач" className={cn("gap-3 px-5 py-4", list ? "flex flex-col" : "flex overflow-hidden")}>
        {Array.from({ length: list ? 6 : 4 }, (_, index) => <Skeleton key={index} className={list ? "h-10 w-full" : "h-56 w-[300px] shrink-0"} />)}
    </div>;
}

/** Единая загрузка, фильтры и создание для двух представлений задач проекта. */
export function TasksPage() {
    const project = useProjectOutlet();
    const selectedTaskId = useUiStore(state => state.selectedTaskId);
    const setSelectedTaskId = useUiStore(state => state.setSelectedTaskId);
    const [params] = useSearchParams();
    const location = useLocation();
    const navigate = useNavigate();
    const [isCreateOpen, setCreateOpen] = useState(false);
    const view = params.get("view") === "list" ? "list" : "kanban";
    const search = params.get("search") ?? "";
    const stageFilter = params.get("stage") ?? "";
    const priorityFilter = params.get("priority") ?? "";
    const deferredSearch = useDeferredValue(search.trim());

    function setFilter(name: string, value: string) {
        const next = new URLSearchParams(params);
        if (value) next.set(name, value); else next.delete(name);
        navigate({ search: next.toString(), hash: location.hash }, { replace: true });
    }

    const stagesQuery = useQuery({
        queryKey: queryKeys.stages(project.id), staleTime: 30_000,
        queryFn: () => api.get<ProjectStage[]>(endpoints.projectStages(project.id)),
    });
    const tasksQuery = useQuery({
        queryKey: queryKeys.tasks(project.id, deferredSearch), staleTime: 30_000,
        queryFn: ({ signal }) => api.get<TaskListItem[]>(deferredSearch
            ? `${endpoints.projectTasks(project.id)}?search=${encodeURIComponent(deferredSearch)}`
            : endpoints.projectTasks(project.id), { signal }),
    });

    const visibleTasks = useMemo(() => {
        const stageOrder = new Map((stagesQuery.data ?? []).map(stage => [stage.id, stage.order_index]));
        return (tasksQuery.data ?? [])
            .filter(task => stageFilter === "" || task.stage_id === Number(stageFilter))
            .filter(task => priorityFilter === "" || task.priority === priorityFilter)
            .sort((first, second) => {
                const stageDelta = (stageOrder.get(first.stage_id ?? 0) ?? 0) - (stageOrder.get(second.stage_id ?? 0) ?? 0);
                return stageDelta || first.position - second.position;
            });
    }, [tasksQuery.data, stagesQuery.data, stageFilter, priorityFilter]);

    const error = stagesQuery.error ?? tasksQuery.error;
    const pending = stagesQuery.isPending || tasksQuery.isPending;
    const filtered = Boolean(deferredSearch || stageFilter || priorityFilter);

    return <div className="flex h-full min-w-0 flex-col">
        <div className="material-metal flex shrink-0 flex-wrap items-center gap-2 border-b border-line-subtle px-3 py-2.5 sm:px-5">
            <nav aria-label="Представление задач" className="order-1 flex shrink-0 rounded-[var(--radius-control)] border border-line-subtle bg-app/25 p-0.5">
                {VIEWS.map(({ id, label, icon: Icon }) => {
                    const next = new URLSearchParams(params);
                    if (id === "kanban") next.delete("view"); else next.set("view", id);
                    return <Link key={id} to={{ search: next.toString(), hash: location.hash }} aria-current={view === id ? "page" : undefined}
                        className={cn("brand-tab touch-target flex min-h-8 items-center gap-1.5 rounded-[var(--radius-control)] px-2.5 text-[13px] focus-visible:outline-2 focus-visible:outline-accent",
                            view === id ? "bg-surface-2 text-primary shadow-card" : "text-muted hover:bg-hover hover:text-primary")}>
                        <Icon size={15} aria-hidden="true" />{label}
                    </Link>;
                })}
            </nav>
            <div className="relative order-3 min-w-0 flex-1 basis-full sm:order-2 sm:max-w-xs sm:basis-48">
                <Search size={14} aria-hidden="true" className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-disabled" />
                <Input value={search} aria-label="Поиск задач проекта" placeholder="Название, описание, комментарий, номер" className="pl-8"
                    onChange={event => setFilter("search", event.target.value)} />
            </div>
            <Select aria-label="Фильтр по стадии" value={stageFilter} className="order-4 min-w-0 flex-1 basis-0 sm:order-3 sm:max-w-44 sm:basis-36" onChange={event => setFilter("stage", event.target.value)}>
                <option value="">Все стадии</option>
                {stagesQuery.data?.map(stage => <option key={stage.id} value={stage.id}>{stage.name}</option>)}
            </Select>
            <Select aria-label="Фильтр по приоритету" value={priorityFilter} className="order-5 min-w-0 flex-1 basis-0 sm:order-4 sm:max-w-44 sm:basis-36" onChange={event => setFilter("priority", event.target.value)}>
                <option value="">Все приоритеты</option>
                {PRIORITY_ORDER.map(priority => <option key={priority} value={priority}>{PRIORITY_LABELS[priority]}</option>)}
            </Select>
            <Button variant="primary" className="order-2 ml-auto sm:order-5" icon={<Plus size={15} />} onClick={() => setCreateOpen(true)}>Задача</Button>
        </div>

        {error ? <div className="p-5"><ErrorMessage message={(error as Error).message} /></div>
            : pending ? <TasksSkeleton list={view === "list"} />
            : visibleTasks.length === 0 ? <div className="p-5"><EmptyState
                title={filtered ? "Ничего не найдено" : "В проекте пока нет задач"}
                description={filtered ? "Измените запрос или снимите фильтры." : "Создайте первую задачу, чтобы начать работу."}
                icon={<ListTodo size={24} />}
                action={filtered ? undefined : <Button variant="primary" icon={<Plus size={15} />} onClick={() => setCreateOpen(true)}>Создать задачу</Button>}
            /></div>
            : view === "list" ? <TasksTable tasks={visibleTasks} stages={stagesQuery.data ?? []} selectedTaskId={selectedTaskId} onTaskOpen={setSelectedTaskId} />
            : <div className="min-h-0 flex-1"><Board projectId={project.id} stages={stagesQuery.data ?? []} tasks={visibleTasks} allTasks={tasksQuery.data}
                search={deferredSearch} selectedTaskId={selectedTaskId} onTaskOpen={setSelectedTaskId} /></div>}

        <CreateTaskDialog projectId={project.id} stages={stagesQuery.data ?? []} isOpen={isCreateOpen} onClose={() => setCreateOpen(false)} onCreated={task => setSelectedTaskId(task.id)} />
    </div>;
}
