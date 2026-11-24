import { forwardRef } from "react";
import { UserIdentity } from "@/components/users/UserIdentity";
import { AlertTriangle, MessageSquare } from "lucide-react";
import type { ProjectStage, TaskListItem } from "@/lib/types";
import { cn } from "@/lib/cn";
import { PriorityBadge } from "@/components/ui/Badge";
import { DueDate } from "@/components/ui/DueDate";
import { BrandIcon } from "@/components/ui/BrandIcon";
import { CopyTaskTitleButton } from "@/components/tasks/CopyTaskTitleButton";
import { SearchHighlight } from "@/components/ui/SearchHighlight";

/** Убирает разметку Markdown: в карточке показываем текст, а не сырой синтаксис. */
function toPlainText(markdown: string): string {
    return markdown
        .replace(/```[\s\S]*?```/g, " ")
        .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
        .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
        .replace(/[#>*_`~-]/g, " ")
        .replace(/\s+/g, " ")
        .trim();
}

interface TaskCardProps {
    task: TaskListItem;
    riskCount?: number;
    breadcrumb?: string | null;
    isDone?: boolean;
    isSelected?: boolean;
    isDragging?: boolean;
    onOpen: (taskId: number) => void;
    className?: string;
    style?: React.CSSProperties;
    dragHandleProps?: Record<string, unknown>;
    stages?: ProjectStage[];
    moving?: boolean;
    onMove?: (stageId: number) => void;
}

/**
 * Карточка канбана по макету раздела 8: понятна без открытия и остаётся
 * компактной. Отсутствующие данные не оставляют пустых блоков.
 */
export const TaskCard = forwardRef<HTMLDivElement, TaskCardProps>(function TaskCard(
    {
        task,
        riskCount = 0,
        breadcrumb,
        isDone = false,
        isSelected = false,
        isDragging = false,
        onOpen,
        className,
        style,
        dragHandleProps,
        stages,
        moving,
        onMove,
    },
    ref,
) {
    const description = task.description_preview ? toPlainText(task.description_preview) : "";
    const executor = task.participants?.find(person => person.role === "EXECUTOR")?.user;

    return (
        <div
            ref={ref}
            style={style}
            {...dragHandleProps}
            className={cn(
                "surface-card group rounded-[var(--radius-card)] border shadow-card",
                !isSelected && !isDragging && "kanban-card-surface",
                "transition-[background-color,border-color,box-shadow,transform]",
                "duration-[var(--duration-normal)] ease-[var(--ease-standard)]",
                "focus-within:border-accent/55 focus-within:shadow-selected",
                isSelected
                    ? "border-accent/55 bg-elevated shadow-selected"
                    : !isDragging && "border-line",
                !isDragging && "hover:-translate-y-px hover:border-line-strong hover:shadow-elevated",
                isDragging && "brand-kanban-dragging shadow-dragging",
                className,
            )}
        >
            <div className="flex items-center justify-between gap-2 px-3.5 pt-2">
                <div className="flex min-w-0 flex-wrap items-center gap-1 font-mono text-[11px] text-muted">
                    <button type="button" onClick={() => onOpen(task.id)} className="touch-target shrink-0 hover:text-primary">{task.key}</button>
                    <span className="contents" onPointerDown={event => event.stopPropagation()} onMouseDown={event => event.stopPropagation()} onTouchStart={event => event.stopPropagation()} onKeyDown={event => event.stopPropagation()}>
                        <CopyTaskTitleButton taskKey={task.key} title={task.title} />
                    </span>
                    {riskCount > 0 && <span aria-label={`Активных рисков: ${riskCount}`} title={`Активных рисков: ${riskCount}`} className="inline-flex items-center gap-1 text-warning"><AlertTriangle size={11} aria-hidden="true" />{riskCount}</span>}
                </div>
                <PriorityBadge priority={task.priority} />
            </div>
            <button
                type="button"
                onClick={() => onOpen(task.id)}
                className="flex w-full min-w-0 flex-col gap-2 px-3.5 pt-1 pb-3 text-left outline-none"
            >
                <h3
                    className={cn(
                        "line-clamp-2 text-[14px] leading-snug font-semibold break-words",
                        isDone ? "text-muted line-through" : "text-primary",
                    )}
                >
                    <SearchHighlight text={task.search_title ?? task.title} />
                </h3>

                {description && (
                    <p className="line-clamp-2 text-[12px] leading-relaxed text-muted">
                        {task.search_excerpt ? (
                            <SearchHighlight text={task.search_excerpt} />
                        ) : (
                            description
                        )}
                    </p>
                )}

                {task.last_comment && (
                    <p
                        className={cn(
                            "line-clamp-2 rounded-sm border-l-2 border-accent-border/70 bg-white/[0.025]",
                            "px-2 py-1.5 text-[12px] leading-relaxed text-muted",
                        )}
                    >
                        {toPlainText(task.last_comment)}
                    </p>
                )}

                {breadcrumb && (
                    <p className="truncate text-[11px] text-disabled">{breadcrumb}</p>
                )}

                {(task.due_date !== null || task.comments_count > 0) && (
                    <div className="flex w-full min-w-0 items-center justify-between gap-2 pt-0.5">
                        <DueDate value={task.due_date} isDone={isDone} />
                        {task.comments_count > 0 && (
                            <span
                                className="ml-auto inline-flex shrink-0 items-center gap-1 text-[11px] text-muted"
                                title={`Комментариев: ${task.comments_count}`}
                            >
                                <BrandIcon icon={MessageSquare} size={11} />
                                {task.comments_count}
                            </span>
                        )}
                    </div>
                )}
                {(task.assignee || executor) && (
                    <UserIdentity user={executor} name={executor ? undefined : task.assignee}
                        className="w-full items-start text-[11px] text-muted"
                        nameClassName="overflow-visible text-clip whitespace-normal break-words" />
                )}
            </button>
            {stages && onMove && (
                <label className="kanban-card-stage" onPointerDown={event => event.stopPropagation()} onTouchStart={event => event.stopPropagation()} onMouseDown={event => event.stopPropagation()} onKeyDown={event => event.stopPropagation()}>
                    <span>Стадия</span>
                    <select aria-label={`Стадия задачи ${task.key}`} value={task.stage_id ?? ""} disabled={moving} onChange={event => onMove(Number(event.target.value))}>
                        {task.stage_id === null && <option value="" disabled>Без стадии</option>}
                        {stages.map(stage => <option key={stage.id} value={stage.id}>{stage.name}</option>)}
                    </select>
                </label>
            )}
        </div>
    );
});
