import { useDeferredValue, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ListTodo, Plus, Search } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { ProjectStage, Task, TaskPriority } from "@/lib/types";
import { PRIORITY_LABELS, PRIORITY_ORDER } from "@/lib/types";
import { cn } from "@/lib/cn";
import { useProjectOutlet } from "@/lib/useProjectOutlet";
import { useUiStore } from "@/stores/ui";
import { Button } from "@/components/ui/Button";
import { Input, Select } from "@/components/ui/Field";
import { PriorityBadge, StatusDot } from "@/components/ui/Badge";
import { DueDate } from "@/components/ui/DueDate";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";
import { SearchHighlight } from "@/components/ui/SearchHighlight";
import { CreateTaskDialog } from "@/components/tasks/CreateTaskDialog";

/**
 * Табличное представление задач (раздел 12): те же статусы, приоритеты
 * и токены, что и на канбане — задача остаётся узнаваемой.
 */
export function TasksListPage() {
    const project = useProjectOutlet();
    const selectedTaskId = useUiStore((state) => state.selectedTaskId);
    const setSelectedTaskId = useUiStore((state) => state.setSelectedTaskId);
    const [search, setSearch] = useState("");
    const [stageFilter, setStageFilter] = useState("");
    const [priorityFilter, setPriorityFilter] = useState("");
    const [isCreateOpen, setCreateOpen] = useState(false);
    const deferredSearch = useDeferredValue(search.trim());

    const stagesQuery = useQuery({
        queryKey: queryKeys.stages(project.id),
        queryFn: () => api.get<ProjectStage[]>(endpoints.projectStages(project.id)),
    });

    const tasksQuery = useQuery({
        queryKey: queryKeys.tasks(project.id, deferredSearch),
        queryFn: () =>
            api.get<Task[]>(
                deferredSearch
                    ? `${endpoints.projectTasks(project.id)}?search=${encodeURIComponent(deferredSearch)}`
                    : endpoints.projectTasks(project.id),
            ),
    });

    const stagesById = useMemo(
        () => new Map((stagesQuery.data ?? []).map((stage) => [stage.id, stage])),
        [stagesQuery.data],
    );

    const rows = useMemo(() => {
        const stageOrder = new Map(
            (stagesQuery.data ?? []).map((stage) => [stage.id, stage.order_index]),
        );
        return (tasksQuery.data ?? [])
            .filter((task) => stageFilter === "" || task.stage_id === Number(stageFilter))
            .filter((task) => priorityFilter === "" || task.priority === priorityFilter)
            .sort((first, second) => {
                const stageDelta =
                    (stageOrder.get(first.stage_id ?? 0) ?? 0) - (stageOrder.get(second.stage_id ?? 0) ?? 0);
                return stageDelta !== 0 ? stageDelta : first.position - second.position;
            });
    }, [tasksQuery.data, stagesQuery.data, stageFilter, priorityFilter]);

    const error = stagesQuery.error ?? tasksQuery.error;

    return (
        <div className="scrollbar-thin h-full overflow-y-auto">
            <div className="mx-auto flex w-full min-w-0 max-w-none flex-col gap-4 px-3 py-4 pb-24 sm:px-5 sm:pb-4">
                <div className="material-metal -mx-2 flex flex-wrap items-center gap-2 rounded-[var(--radius-card)] border border-line-subtle px-2 py-2 shadow-card">
                    <div className="relative w-full min-w-0 sm:max-w-xs sm:flex-1">
                        <Search
                            size={14}
                            aria-hidden="true"
                            className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-disabled"
                        />
                        <Input
                            value={search}
                            aria-label="Поиск задач проекта"
                            placeholder="Название, описание, номер"
                            className="pl-8"
                            onChange={(event) => setSearch(event.target.value)}
                        />
                    </div>
                    <Select
                        aria-label="Фильтр по стадии"
                        value={stageFilter}
                        className="w-full min-w-0 sm:w-auto sm:min-w-36"
                        onChange={(event) => setStageFilter(event.target.value)}
                    >
                        <option value="">Все стадии</option>
                        {stagesQuery.data?.map((stage) => (
                            <option key={stage.id} value={stage.id}>
                                {stage.name}
                            </option>
                        ))}
                    </Select>
                    <Select
                        aria-label="Фильтр по приоритету"
                        value={priorityFilter}
                        className="w-full min-w-0 sm:w-auto sm:min-w-36"
                        onChange={(event) => setPriorityFilter(event.target.value)}
                    >
                        <option value="">Все приоритеты</option>
                        {PRIORITY_ORDER.map((priority: TaskPriority) => (
                            <option key={priority} value={priority}>
                                {PRIORITY_LABELS[priority]}
                            </option>
                        ))}
                    </Select>
                    <Button
                        variant="primary"
                        icon={<Plus size={15} />}
                        onClick={() => setCreateOpen(true)}
                    >
                        Задача
                    </Button>
                </div>

                {error && <ErrorMessage message={(error as Error).message} />}

                {tasksQuery.isPending && (
                    <div role="status" aria-label="Загрузка задач" className="flex flex-col gap-1.5">
                        {Array.from({ length: 8 }).map((_, index) => (
                            <Skeleton key={index} className="h-10 w-full" />
                        ))}
                    </div>
                )}

                {tasksQuery.data && rows.length === 0 && (
                    <EmptyState
                        title={deferredSearch ? "Ничего не найдено" : "Задач пока нет"}
                        description={
                            deferredSearch
                                ? "Измените запрос или снимите фильтры."
                                : "Создайте первую задачу, чтобы начать работу."
                        }
                        icon={<ListTodo size={24} />}
                    />
                )}

                {rows.length > 0 && (
                    <div className="scrollbar-thin overflow-x-auto rounded-[var(--radius-card)] md:border md:border-line-subtle md:bg-surface/55 md:shadow-card">
                        <table className="mobile-data-table w-full border-collapse text-left md:min-w-[720px]">
                            <thead>
                                <tr className="border-b border-line-subtle bg-surface-2/65">
                                    {["ID", "Задача", "Стадия", "Приоритет", "Исполнитель", "Срок"].map(
                                        (title) => (
                                            <th
                                                key={title}
                                                scope="col"
                                                className="px-3 py-2 text-[11px] font-semibold tracking-[0.06em] text-muted uppercase"
                                            >
                                                {title}
                                            </th>
                                        ),
                                    )}
                                </tr>
                            </thead>
                            <tbody>
                                {rows.map((task) => {
                                    const stage = stagesById.get(task.stage_id ?? 0);
                                    return (
                                        <tr
                                            key={task.id}
                                            className={cn(
                                                "border-b border-line-subtle last:border-b-0",
                                                "transition-colors duration-[var(--duration-fast)]",
                                                task.id === selectedTaskId
                                                    ? "bg-accent-soft"
                                                    : "hover:bg-hover",
                                            )}
                                        >
                                            <td className="px-3 py-2">
                                                <button
                                                    type="button"
                                                    onClick={() => setSelectedTaskId(task.id)}
                                                    className="font-mono text-[11px] text-muted hover:text-accent"
                                                >
                                                    {task.key}
                                                </button>
                                            </td>
                                            <td className="max-w-md px-3 py-2">
                                                <button
                                                    type="button"
                                                    onClick={() => setSelectedTaskId(task.id)}
                                                    className={cn(
                                                        "block w-full text-left text-[14px] break-words md:truncate md:text-[13px]",
                                                        stage?.is_done_stage
                                                            ? "text-muted line-through"
                                                            : "text-secondary hover:text-primary",
                                                    )}
                                                >
                                                    <SearchHighlight
                                                        text={task.search_title ?? task.title}
                                                    />
                                                </button>
                                            </td>
                                            <td data-label="Стадия" className="px-3 py-2">
                                                {stage && (
                                                    <span className="inline-flex items-center gap-1.5 text-[12px] text-muted">
                                                        <StatusDot color={stage.color} />
                                                        {stage.name}
                                                    </span>
                                                )}
                                            </td>
                                            <td data-label="Приоритет" className="px-3 py-2">
                                                <PriorityBadge priority={task.priority} showLow />
                                            </td>
                                            <td data-label="Исполнитель" className="px-3 py-2 text-[12px] text-muted">
                                                {task.assignee ?? "—"}
                                            </td>
                                            <td className="px-3 py-2">
                                                <DueDate
                                                    value={task.due_date}
                                                    isDone={stage?.is_done_stage}
                                                />
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            <CreateTaskDialog
                projectId={project.id}
                stages={stagesQuery.data ?? []}
                isOpen={isCreateOpen}
                onClose={() => setCreateOpen(false)}
                onCreated={(task) => setSelectedTaskId(task.id)}
            />
        </div>
    );
}
