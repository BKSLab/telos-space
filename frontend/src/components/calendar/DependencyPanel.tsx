import { ArrowRight, GitBranch, Plus, Trash2 } from "lucide-react";
import { IconButton } from "@/components/ui/Button";
import type { Task, TaskDependency } from "@/lib/types";

interface DependencyPanelProps {
    dependencies: TaskDependency[];
    tasks: Task[];
    isSaving: boolean;
    /** Нет — только просмотр, кнопки добавления нет. */
    onCreate?: () => void;
    onDelete: (dependency: TaskDependency) => void;
}

/** Компактный список связей, которые нарисованы поверх timeline. */
export function DependencyPanel({
    dependencies,
    tasks,
    isSaving,
    onCreate,
    onDelete,
}: DependencyPanelProps) {
    const tasksById = new Map(tasks.map((task) => [task.id, task]));
    return (
        <section className="rounded-[var(--radius-card)] bg-surface/55 p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                    <GitBranch size={13} className="text-accent" aria-hidden="true" />
                    <h3 className="text-[11px] font-semibold tracking-[0.06em] text-muted uppercase">
                        Зависимости
                    </h3>
                </div>
                {onCreate && <IconButton label="Добавить зависимость" size="sm" onClick={onCreate}>
                    <Plus size={13} aria-hidden="true" />
                </IconButton>}
            </div>
            {dependencies.length === 0 && (
                <p className="py-2 text-[11px] text-muted">Связей между задачами пока нет.</p>
            )}
            <div className="flex flex-col gap-1 lg:max-h-44 lg:overflow-y-auto">
                {dependencies.map((dependency) => {
                    const predecessor = tasksById.get(dependency.predecessor_task_id);
                    const successor = tasksById.get(dependency.successor_task_id);
                    return (
                        <div
                            key={dependency.id}
                            className="group flex items-center gap-1.5 rounded-[var(--radius-control)] px-2 py-1.5 transition-colors hover:bg-hover"
                            title="Finish-to-Start"
                        >
                            <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-secondary">
                                {predecessor?.key ?? `#${dependency.predecessor_task_id}`}
                                <span className="line-clamp-2 font-sans text-[11px] whitespace-normal text-muted lg:hidden">{predecessor?.title}</span>
                            </span>
                            <ArrowRight size={11} className="shrink-0 text-accent" />
                            <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-secondary">
                                {successor?.key ?? `#${dependency.successor_task_id}`}
                                <span className="line-clamp-2 font-sans text-[11px] whitespace-normal text-muted lg:hidden">{successor?.title}</span>
                            </span>
                            {dependency.lag_days > 0 && (
                                <span className="shrink-0 text-[9px] text-muted">
                                    +{dependency.lag_days}д
                                </span>
                            )}
                            <IconButton
                                label="Удалить зависимость"
                                size="sm"
                                disabled={isSaving}
                                className="text-danger opacity-100 lg:opacity-0 lg:group-hover:opacity-100 focus-visible:opacity-100"
                                onClick={() => onDelete(dependency)}
                            >
                                <Trash2 size={11} aria-hidden="true" />
                            </IconButton>
                        </div>
                    );
                })}
            </div>
        </section>
    );
}
