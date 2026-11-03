import { useMemo, useState } from "react";
import { Inbox, Plus, Search } from "lucide-react";
import { cn } from "@/lib/cn";
import type { ProjectStage, TaskCompact, TaskPriority } from "@/lib/types";
import { PRIORITY_LABELS, PRIORITY_ORDER } from "@/lib/types";
import { dueTone } from "@/lib/dates";
import { isFloatingTask } from "@/lib/wbsTree";
import { Button } from "@/components/ui/Button";
import { Input, Select } from "@/components/ui/Field";
import { DueDate } from "@/components/ui/DueDate";
import { PriorityBadge, StatusDot } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/States";

export type PoolScope = "unassigned" | "all";

/** Задача в списке-пуле: не в структуре и не выложена на холст. */
function isInPool(task: TaskCompact): boolean {
    return task.wbs_node_id === null && !isFloatingTask(task);
}

/** Тип полезной нагрузки перетаскивания: и пул, и canvas читают один ключ. */
export const TASK_DRAG_TYPE = "text/plain";

/**
 * Метка списка задач в DOM. Перетаскивание карточки внутри canvas не порождает
 * HTML5-события, поэтому canvas ищет границы пула по этому атрибуту.
 */
export const POOL_DROP_ATTRIBUTE = "data-wbs-task-pool";

interface TaskPoolProps {
    tasks: TaskCompact[];
    stages: ProjectStage[];
    onOpenTask: (taskId: number) => void;
    onMoveTask: (taskId: number) => void;
    onCreateTask: () => void;
    /** Начало перетаскивания задачи из пула на canvas. */
    onDragStart: (task: TaskCompact) => void;
    onDragEnd: () => void;
    isDropTarget: boolean;
}

/**
 * Левая панель со списком задач (§14 ТЗ). По умолчанию показывает только
 * нераспределённые; переключатель «Все задачи» помогает найти уже
 * размещённую задачу и перенести её в другой раздел.
 */
export function TaskPool({
    tasks,
    stages,
    onOpenTask,
    onMoveTask,
    onCreateTask,
    onDragStart,
    onDragEnd,
    isDropTarget,
}: TaskPoolProps) {
    const [scope, setScope] = useState<PoolScope>("unassigned");
    const [search, setSearch] = useState("");
    const [stageFilter, setStageFilter] = useState("");
    const [priorityFilter, setPriorityFilter] = useState("");
    const [dueFilter, setDueFilter] = useState("");

    const stagesById = useMemo(
        () => new Map(stages.map((stage) => [stage.id, stage])),
        [stages],
    );

    const visible = useMemo(() => {
        const query = search.trim().toLowerCase();
        return tasks
            // Карточка, выложенная на холст, живёт там, а не в списке.
            .filter((task) => (scope === "unassigned" ? isInPool(task) : true))
            .filter(
                (task) =>
                    query === "" ||
                    task.title.toLowerCase().includes(query) ||
                    task.key.toLowerCase().includes(query),
            )
            .filter((task) => stageFilter === "" || task.stage_id === Number(stageFilter))
            .filter((task) => priorityFilter === "" || task.priority === priorityFilter)
            .filter((task) => {
                if (dueFilter === "") {
                    return true;
                }
                const tone = dueTone(task.due_date, task.is_done);
                return dueFilter === "overdue" ? tone === "danger" : tone === "warning";
            })
            .sort((first, second) => first.key.localeCompare(second.key, "ru", { numeric: true }));
    }, [tasks, scope, search, stageFilter, priorityFilter, dueFilter]);

    const unassignedCount = tasks.filter(isInPool).length;

    return (
        <aside
            aria-label="Задачи проекта"
            {...{ [POOL_DROP_ATTRIBUTE]: "" }}
            className={cn(
                "wbs-task-pool relative flex min-h-0 w-full shrink-0 flex-col border-r bg-sidebar lg:w-72",
                "transition-[background-color,border-color] duration-[var(--duration-fast)]",
                // Карточку, поднесённую к списку, обрезает граница холста,
                // поэтому готовность принять её показывает сам список.
                isDropTarget ? "border-accent/60 bg-accent/[0.08]" : "border-line",
            )}
        >
            {isDropTarget && (
                <span
                    aria-hidden="true"
                    className="pointer-events-none absolute inset-1 rounded-[var(--radius-card)] border-2 border-dashed border-accent/50"
                />
            )}
            <div className="flex shrink-0 flex-col gap-2 border-b border-line px-3 py-2.5">
                <div className="flex items-center justify-between gap-2">
                    <h2 className="text-[11px] font-semibold tracking-[0.06em] text-muted uppercase">
                        {scope === "unassigned" ? "Не в структуре" : "Все задачи"}
                    </h2>
                    <span className="font-mono text-[11px] text-disabled">
                        {scope === "unassigned" ? unassignedCount : tasks.length}
                    </span>
                </div>

                <div className="flex gap-1" role="group" aria-label="Область списка задач">
                    {(["unassigned", "all"] as const).map((value) => (
                        <button
                            key={value}
                            type="button"
                            aria-pressed={scope === value}
                            onClick={() => setScope(value)}
                            className={cn(
                                "flex-1 rounded-sm px-2 py-1 text-[11px] font-medium",
                                "transition-colors duration-[var(--duration-fast)]",
                                scope === value
                                    ? "bg-accent-soft text-accent"
                                    : "text-muted hover:bg-hover hover:text-secondary",
                            )}
                        >
                            {value === "unassigned" ? "В пуле" : "Все"}
                        </button>
                    ))}
                </div>

                <div className="relative">
                    <Search
                        size={13}
                        aria-hidden="true"
                        className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-disabled"
                    />
                    <Input
                        value={search}
                        aria-label="Поиск по задачам пула"
                        placeholder="Название или номер"
                        className="pl-7"
                        onChange={(event) => setSearch(event.target.value)}
                    />
                </div>

                <div className="grid grid-cols-2 gap-1">
                    <Select
                        aria-label="Фильтр по стадии"
                        value={stageFilter}
                        className="px-1.5 text-[11px]"
                        onChange={(event) => setStageFilter(event.target.value)}
                    >
                        <option value="">Любая стадия</option>
                        {stages.map((stage) => (
                            <option key={stage.id} value={stage.id}>
                                {stage.name}
                            </option>
                        ))}
                    </Select>
                    <Select
                        aria-label="Фильтр по приоритету"
                        value={priorityFilter}
                        className="px-1.5 text-[11px]"
                        onChange={(event) => setPriorityFilter(event.target.value)}
                    >
                        <option value="">Любой приоритет</option>
                        {PRIORITY_ORDER.map((priority: TaskPriority) => (
                            <option key={priority} value={priority}>
                                {PRIORITY_LABELS[priority]}
                            </option>
                        ))}
                    </Select>
                    <Select
                        aria-label="Фильтр по сроку"
                        value={dueFilter}
                        className="col-span-2 px-1.5 text-[11px]"
                        onChange={(event) => setDueFilter(event.target.value)}
                    >
                        <option value="">Любой срок</option>
                        <option value="overdue">Просрочен</option>
                        <option value="soon">Ближайшая неделя</option>
                    </Select>
                </div>
            </div>

            <div className="scrollbar-thin flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto p-2">
                {visible.length === 0 ? (
                    <EmptyState
                        title={
                            scope === "unassigned" && tasks.length > 0
                                ? "Все задачи распределены"
                                : "Задач нет"
                        }
                        description={
                            scope === "unassigned" && tasks.length > 0
                                ? "Переключитесь на «Все», чтобы перенести задачу в другой раздел."
                                : undefined
                        }
                        icon={<Inbox size={20} />}
                        className="border-none py-8"
                    />
                ) : (
                    visible.map((task) => (
                        <div
                            key={task.id}
                            draggable
                            onDragStart={(event) => {
                                event.dataTransfer.effectAllowed = "move";
                                event.dataTransfer.setData(TASK_DRAG_TYPE, String(task.id));
                                onDragStart(task);
                            }}
                            onDragEnd={onDragEnd}
                            className={cn(
                                "group cursor-grab rounded-md border border-line-subtle bg-surface px-2.5 py-2",
                                "transition-[background-color,border-color] duration-[var(--duration-fast)]",
                                "hover:border-line-strong hover:bg-surface-2 active:cursor-grabbing",
                            )}
                        >
                            <div className="flex items-center justify-between gap-2">
                                <button
                                    type="button"
                                    onClick={() => onOpenTask(task.id)}
                                    className="font-mono text-[10px] text-muted hover:text-accent"
                                >
                                    {task.key}
                                </button>
                                <PriorityBadge priority={task.priority} />
                            </div>

                            <button
                                type="button"
                                onClick={() => onOpenTask(task.id)}
                                className={cn(
                                    "mt-0.5 line-clamp-2 w-full text-left text-[12px] leading-snug",
                                    task.is_done ? "text-muted line-through" : "text-secondary",
                                )}
                            >
                                {task.title}
                            </button>

                            <div className="mt-1 flex items-center justify-between gap-2 text-[10px] text-muted">
                                <span className="inline-flex min-w-0 items-center gap-1">
                                    <StatusDot
                                        color={
                                            stagesById.get(task.stage_id)?.color ??
                                            "var(--color-text-muted)"
                                        }
                                    />
                                    <span className="truncate">
                                        {stagesById.get(task.stage_id)?.name ?? "—"}
                                    </span>
                                </span>
                                <DueDate value={task.due_date} isDone={task.is_done} />
                            </div>

                            <button
                                type="button"
                                onClick={() => onMoveTask(task.id)}
                                className={cn(
                                    "wbs-pool-move mt-1.5 w-full rounded-sm border border-line-subtle px-2 py-1 text-[10px]",
                                    "text-muted opacity-0 transition-opacity hover:text-primary",
                                    "group-hover:opacity-100 focus-visible:opacity-100",
                                )}
                            >
                                Переместить в раздел…
                            </button>
                        </div>
                    ))
                )}
            </div>

            <div className="shrink-0 border-t border-line p-2">
                <Button
                    className="w-full"
                    size="sm"
                    icon={<Plus size={14} />}
                    onClick={onCreateTask}
                >
                    Новая задача
                </Button>
            </div>
        </aside>
    );
}
