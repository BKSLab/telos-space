import type { ProjectStage, TaskListItem } from "@/lib/types";
import { cn } from "@/lib/cn";
import { UserIdentity } from "@/components/users/UserIdentity";
import { PriorityBadge, StatusDot } from "@/components/ui/Badge";
import { DueDate } from "@/components/ui/DueDate";
import { SearchHighlight } from "@/components/ui/SearchHighlight";

interface TasksTableProps {
    tasks: TaskListItem[];
    stages: ProjectStage[];
    selectedTaskId: number | null;
    onTaskOpen: (taskId: number) => void;
}

/** Список отображает те же подготовленные задачи, что и канбан. */
export function TasksTable({ tasks, stages, selectedTaskId, onTaskOpen }: TasksTableProps) {
    const stagesById = new Map(stages.map(stage => [stage.id, stage]));

    return (
        <div className="scrollbar-thin min-h-0 flex-1 overflow-auto px-3 py-4 pb-24 sm:px-5 sm:pb-4">
            <div className="rounded-[var(--radius-card)] md:border md:border-line-subtle md:bg-surface/55 md:shadow-card">
                <table className="mobile-data-table w-full border-collapse text-left md:min-w-[720px]">
                    <thead>
                        <tr className="border-b border-line-subtle bg-surface-2/65">
                            {["ID", "Задача", "Стадия", "Приоритет", "Исполнитель", "Срок"].map(title => (
                                <th key={title} scope="col" className="px-3 py-2 text-[11px] font-semibold tracking-[0.06em] text-muted uppercase">
                                    {title}
                                </th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {tasks.map(task => {
                            const stage = stagesById.get(task.stage_id ?? 0);
                            const executor = task.participants?.find(person => person.role === "EXECUTOR")?.user;
                            return (
                                <tr key={task.id} className={cn(
                                    "border-b border-line-subtle last:border-b-0 transition-colors duration-[var(--duration-fast)]",
                                    task.id === selectedTaskId ? "bg-accent-soft" : "hover:bg-hover",
                                )}>
                                    <td className="px-3 py-2">
                                        <button type="button" onClick={() => onTaskOpen(task.id)} className="font-mono text-[11px] text-muted hover:text-accent">
                                            {task.key}
                                        </button>
                                    </td>
                                    <td className="max-w-md px-3 py-2">
                                        <button type="button" onClick={() => onTaskOpen(task.id)} className={cn(
                                            "block w-full text-left text-[14px] break-words md:truncate md:text-[13px]",
                                            stage?.is_done_stage ? "text-muted line-through" : "text-secondary hover:text-primary",
                                        )}>
                                            <SearchHighlight text={task.search_title ?? task.title} />
                                        </button>
                                    </td>
                                    <td data-label="Стадия" className="px-3 py-2">
                                        {stage && <span className="inline-flex items-center gap-1.5 text-[12px] text-muted"><StatusDot color={stage.color} />{stage.name}</span>}
                                    </td>
                                    <td data-label="Приоритет" className="px-3 py-2"><PriorityBadge priority={task.priority} showLow /></td>
                                    <td data-label="Исполнитель" className="px-3 py-2 text-[12px] text-muted">
                                        {executor ? <UserIdentity user={executor} /> : task.assignee || "—"}
                                    </td>
                                    <td className="px-3 py-2"><DueDate value={task.due_date} isDone={stage?.is_done_stage} /></td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
