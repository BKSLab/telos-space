import type { ReactNode } from "react";
import { MoreHorizontal, Unlink } from "lucide-react";
import { cn } from "@/lib/cn";
import type { ProjectStage, TaskCompact } from "@/lib/types";
import { DueDate } from "@/components/ui/DueDate";
import { PriorityBadge, StatusDot } from "@/components/ui/Badge";

export interface TaskCardProps {
    task: TaskCompact;
    stage: ProjectStage | undefined;
    /** Уровень детализации из semantic zoom: при отдалении прячем вторичное. */
    detail: "full" | "compact" | "minimal";
    /** Карточка лежит на холсте и ещё не связана с разделом. */
    isFloating: boolean;
    /** Карточка входит в предложенную ИИ структуру и пока не сохранена. */
    isDraft: boolean;
    /** От раздела сейчас тянут стрелку: карточка ждёт, что связь бросят в неё. */
    isConnecting?: boolean;
    isSelected?: boolean;
    onContextMenu?: (taskId: number, anchor: { x: number; y: number }) => void;
    /** Точки связи узла: их добавляет только карточка на холсте. */
    children?: ReactNode;
}

/**
 * Вид задачи в ИСР (§22 ТЗ): компактная карточка, а не карточка канбана — так
 * на экране помещается заметно больше работ.
 *
 * Вынесена отдельно от узла графа, потому что ту же самую карточку рисует
 * двойник, который едет над списком задач: он должен быть неотличим от
 * настоящей, иначе выглядит как другой объект.
 */
export function TaskCard({
    task,
    stage,
    detail,
    isFloating,
    isDraft,
    isConnecting = false,
    isSelected = false,
    onContextMenu,
    children,
}: TaskCardProps) {
    return (
        <div
            onContextMenu={
                onContextMenu === undefined
                    ? undefined
                    : (event) => {
                          event.preventDefault();
                          onContextMenu(task.id, { x: event.clientX, y: event.clientY });
                      }
            }
            className={cn(
                "wbs-task-card surface-card relative flex h-full w-full cursor-grab flex-col justify-center gap-1 rounded-[var(--radius-control)] border px-2.5 py-2 text-left",
                "transition-[background-color,border-color,box-shadow] duration-[var(--duration-normal)]",
                "ease-[var(--ease-standard)] shadow-card active:cursor-grabbing",
                // Холст тёмный, поэтому карточка всегда светлее фона: иначе
                // она с ним сливается.
                isDraft ? "bg-accent/[0.06]" : isFloating ? "bg-elevated" : "bg-surface-2",
                isConnecting && "border-accent/70 bg-accent/[0.07] shadow-selected",
                isSelected
                    ? "border-accent/60 bg-elevated shadow-selected"
                    : isDraft
                      ? "border-dashed border-accent/55"
                      : isFloating
                        ? "border-dashed border-line-strong"
                        : "border-line hover:border-line-strong hover:bg-elevated",
            )}
        >
            {children}

            {onContextMenu && <button type="button" className="wbs-node-menu nodrag nopan" aria-label={`Действия задачи ${task.key}`} onClick={event => {
                event.stopPropagation();
                const rect = event.currentTarget.getBoundingClientRect();
                onContextMenu(task.id, { x: rect.right, y: rect.bottom });
            }}><MoreHorizontal size={16} aria-hidden="true" /></button>}

            <div className={cn("flex shrink-0 items-center justify-between gap-2", onContextMenu && "pr-7")}>
                <span className="font-mono text-[10px] text-muted">{task.key}</span>
                <div className="flex items-center gap-1">
                    {isFloating && (
                        <Unlink
                            size={10}
                            aria-label="Задача вне структуры"
                            className="text-disabled"
                        />
                    )}
                    <PriorityBadge priority={task.priority} />
                </div>
            </div>

            <p
                className={cn(
                    "line-clamp-1 shrink-0 pr-6 text-[12px] leading-snug",
                    task.is_done ? "text-muted line-through" : "text-secondary",
                )}
            >
                {task.title}
            </p>

            {detail === "full" && (
                <div className="flex shrink-0 items-center justify-between gap-2 text-[10px] text-muted">
                    {stage && (
                        <span className="inline-flex min-w-0 items-center gap-1">
                            <StatusDot color={stage.color} />
                            <span className="truncate">{stage.name}</span>
                        </span>
                    )}
                    <DueDate value={task.due_date} isDone={task.is_done} />
                </div>
            )}
        </div>
    );
}
