import { useMemo } from "react";
import { Check, Sparkles, Trash2, X } from "lucide-react";
import { cn } from "@/lib/cn";
import type { TaskCompact, WbsSuggestion } from "@/lib/types";
import { flattenSuggestion } from "@/lib/wbsSuggestion";
import { Button } from "@/components/ui/Button";

interface SuggestionPanelProps {
    suggestion: WbsSuggestion;
    tasks: TaskCompact[];
    isApplying: boolean;
    onRemoveNode: (tempId: string) => void;
    onRenameNode: (tempId: string, title: string) => void;
    onRemoveAssignment: (taskId: number) => void;
    onApply: () => void;
    onCancel: () => void;
}

/**
 * Черновик ИСР, предложенный моделью (§2 задачи).
 *
 * Панель существует ровно потому, что предложение не применяется само:
 * пользователь выкидывает лишние разделы и задачи, и только потом сохраняет.
 * Пока черновик открыт, он же отрисован на холсте пунктиром.
 */
export function SuggestionPanel({
    suggestion,
    tasks,
    isApplying,
    onRemoveNode,
    onRenameNode,
    onRemoveAssignment,
    onApply,
    onCancel,
}: SuggestionPanelProps) {
    const tasksById = useMemo(() => new Map(tasks.map((task) => [task.id, task])), [tasks]);

    const rows = useMemo(() => flattenSuggestion(suggestion), [suggestion]);

    const assignmentsByNode = useMemo(() => {
        const map = new Map<string, number[]>();
        for (const assignment of suggestion.assignments) {
            const bucket = map.get(assignment.node_temp_id) ?? [];
            bucket.push(assignment.task_id);
            map.set(assignment.node_temp_id, bucket);
        }
        return map;
    }, [suggestion.assignments]);

    return (
        <aside
            aria-label="Предложенная структура ИСР"
            className="wbs-suggestion-panel flex min-h-0 w-full shrink-0 flex-col border-l border-line bg-sidebar lg:w-80"
        >
            <div className="flex shrink-0 items-start gap-2 border-b border-line px-3 py-2.5">
                <Sparkles size={14} className="mt-0.5 text-accent" aria-hidden="true" />
                <div className="min-w-0 flex-1">
                    <h2 className="text-[11px] font-semibold tracking-[0.06em] text-accent uppercase">
                        Предложение ИСР
                    </h2>
                    <p className="text-[10px] text-muted">
                        Черновик: в проекте пока ничего не изменилось
                    </p>
                </div>
                <button
                    type="button"
                    aria-label="Закрыть предложение"
                    onClick={onCancel}
                    className="rounded-sm p-0.5 text-muted hover:bg-hover hover:text-primary"
                >
                    <X size={14} />
                </button>
            </div>

            <div className="scrollbar-thin flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-2.5">
                {suggestion.summary !== "" && (
                    <p className="rounded-md border border-line-subtle bg-surface px-2.5 py-2 text-[11px] leading-relaxed text-secondary">
                        {suggestion.summary}
                    </p>
                )}

                {rows.map(({ node, depth }) => {
                    const taskIds = assignmentsByNode.get(node.temp_id) ?? [];
                    return (
                        <div
                            key={node.temp_id}
                            style={{ marginLeft: depth * 12 }}
                            className="rounded-md border border-dashed border-line-strong bg-surface px-2.5 py-2"
                        >
                            <div className="flex items-start gap-1.5">
                                <input
                                    value={node.title}
                                    aria-label="Название предложенного раздела"
                                    onChange={(event) =>
                                        onRenameNode(node.temp_id, event.target.value)
                                    }
                                    className="min-w-0 flex-1 rounded-sm border border-transparent bg-transparent px-1 py-0.5 text-[12px] font-medium text-primary outline-none hover:border-line focus:border-accent-border focus:bg-surface-2"
                                />
                                <button
                                    type="button"
                                    aria-label={`Убрать раздел ${node.title}`}
                                    onClick={() => onRemoveNode(node.temp_id)}
                                    className="shrink-0 rounded-sm p-0.5 text-muted hover:bg-danger/10 hover:text-danger"
                                >
                                    <Trash2 size={12} />
                                </button>
                            </div>

                            {node.rationale && (
                                <p className="mt-0.5 px-1 text-[10px] leading-snug text-muted">
                                    {node.rationale}
                                </p>
                            )}

                            <ul className="mt-1 flex flex-col">
                                {taskIds.map((taskId) => {
                                    const task = tasksById.get(taskId);
                                    return (
                                        <li
                                            key={taskId}
                                            className="group flex items-center gap-1.5 rounded-sm px-1 py-0.5 hover:bg-hover"
                                        >
                                            <span className="font-mono text-[10px] text-muted">
                                                {task?.key ?? taskId}
                                            </span>
                                            <span className="min-w-0 flex-1 truncate text-[11px] text-secondary">
                                                {task?.title ?? ""}
                                            </span>
                                            <button
                                                type="button"
                                                aria-label={`Не переносить ${task?.key ?? taskId}`}
                                                onClick={() => onRemoveAssignment(taskId)}
                                                className="shrink-0 rounded-sm p-0.5 text-disabled opacity-0 group-hover:opacity-100 hover:text-danger focus-visible:opacity-100"
                                            >
                                                <X size={10} />
                                            </button>
                                        </li>
                                    );
                                })}
                                {taskIds.length === 0 && (
                                    <li className="px-1 text-[10px] text-disabled">
                                        Раздел без задач
                                    </li>
                                )}
                            </ul>
                        </div>
                    );
                })}

                {suggestion.skipped_task_ids.length > 0 && (
                    <p className="px-1 text-[10px] text-muted">
                        Вне предложения остаются задачи: {suggestion.skipped_task_ids.length}. Они
                        не изменятся.
                    </p>
                )}
            </div>

            <div className="flex shrink-0 gap-1.5 border-t border-line p-2">
                <Button className="flex-1" size="sm" onClick={onCancel} disabled={isApplying}>
                    Отменить
                </Button>
                <Button
                    variant="primary"
                    size="sm"
                    icon={<Check size={13} />}
                    className={cn("flex-1", suggestion.nodes.length === 0 && "pointer-events-none")}
                    disabled={isApplying || suggestion.nodes.length === 0}
                    onClick={onApply}
                >
                    {isApplying ? "Применяем…" : "Применить"}
                </Button>
            </div>
        </aside>
    );
}
