import { Check, Diamond, Pencil, Plus, Trash2 } from "lucide-react";
import { Skeleton } from "@/components/ui/States";
import { IconButton } from "@/components/ui/Button";
import { dayTitle } from "@/lib/calendar";
import type { ProjectMilestone } from "@/lib/types";

interface MilestonePanelProps {
    milestones: ProjectMilestone[];
    projectDueDate: string | null;
    isLoading: boolean;
    error: Error | null;
    isSaving: boolean;
    /** Нет — только просмотр, кнопки добавления нет. */
    onCreate?: () => void;
    onEdit: (milestone: ProjectMilestone) => void;
    onDelete: (milestone: ProjectMilestone) => void;
}

/** Управление пользовательскими вехами рядом с временной картой. */
export function MilestonePanel({
    milestones,
    projectDueDate,
    isLoading,
    error,
    isSaving,
    onCreate,
    onEdit,
    onDelete,
}: MilestonePanelProps) {
    return (
        <section className="rounded-[var(--radius-card)] bg-surface/55 p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                    <Diamond size={13} className="text-purple" aria-hidden="true" />
                    <h3 className="text-[11px] font-semibold tracking-[0.06em] text-muted uppercase">
                        Вехи
                    </h3>
                </div>
                {onCreate && <IconButton label="Добавить веху" size="sm" onClick={onCreate}>
                    <Plus size={13} aria-hidden="true" />
                </IconButton>}
            </div>

            {projectDueDate && (
                <div className="mb-1.5 flex items-center gap-2 rounded-md border border-accent/25 bg-accent/5 px-2 py-1.5">
                    <Diamond size={10} className="shrink-0 fill-accent text-accent" />
                    <span className="min-w-0 flex-1 truncate text-[11px] text-secondary">
                        Дедлайн проекта
                    </span>
                    <time className="font-mono text-[10px] text-accent" dateTime={projectDueDate}>
                        {dayTitle(projectDueDate)}
                    </time>
                </div>
            )}

            {isLoading && milestones.length === 0 && <Skeleton className="h-28 w-full" />}
            {error && <p className="py-2 text-[11px] text-danger">{error.message}</p>}
            {!isLoading && !error && milestones.length === 0 && (
                <p className="py-2 text-[11px] text-muted">Пользовательских вех пока нет.</p>
            )}
            <div className="flex flex-col gap-1 lg:max-h-52 lg:overflow-y-auto">
                {milestones.map((milestone) => (
                    <div
                        key={milestone.id}
                        className="group flex items-center gap-1.5 rounded-[var(--radius-control)] px-2 py-1.5 transition-colors hover:bg-hover"
                    >
                        {milestone.status === "ACHIEVED" ? (
                            <Check size={11} className="shrink-0 text-success" aria-hidden="true" />
                        ) : (
                            <Diamond
                                size={10}
                                className="shrink-0 fill-purple/20 text-purple"
                                aria-hidden="true"
                            />
                        )}
                        <button
                            type="button"
                            className="min-w-0 flex-1 text-left"
                            onClick={() => onEdit(milestone)}
                        >
                            <span className="line-clamp-2 text-[12px] text-primary lg:line-clamp-none lg:truncate lg:text-[11px]">
                                {milestone.title}
                            </span>
                            <time
                                className="block font-mono text-[10px] text-muted"
                                dateTime={milestone.due_date}
                            >
                                {dayTitle(milestone.due_date)}
                            </time>
                        </button>
                        <IconButton
                            label={`Изменить веху «${milestone.title}»`}
                            size="sm"
                            disabled={isSaving}
                            className="opacity-100 lg:opacity-0 lg:group-hover:opacity-100 focus-visible:opacity-100"
                            onClick={() => onEdit(milestone)}
                        >
                            <Pencil size={11} aria-hidden="true" />
                        </IconButton>
                        <IconButton
                            label={`Удалить веху «${milestone.title}»`}
                            size="sm"
                            disabled={isSaving}
                            className="text-danger opacity-100 lg:opacity-0 lg:group-hover:opacity-100 focus-visible:opacity-100"
                            onClick={() => onDelete(milestone)}
                        >
                            <Trash2 size={11} aria-hidden="true" />
                        </IconButton>
                    </div>
                ))}
            </div>
        </section>
    );
}
