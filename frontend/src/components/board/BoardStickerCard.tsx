import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";
import { Link2, Pencil, Trash2 } from "lucide-react";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/dates";
import { stickerSurface, type BoardSticker } from "@/lib/board/stickers";
import type { ProjectMember, Task } from "@/lib/types";
import { IconButton } from "@/components/ui/Button";
import { BoardStickerAuthor } from "@/components/board/BoardStickerAuthor";

export interface BoardStickerCardProps {
    sticker: BoardSticker;
    members: ProjectMember[];
    tasksById: Map<number, Task>;
    readOnly: boolean;
    readOnlyHint?: string;
    onEdit: (sticker: BoardSticker) => void;
    onDelete: (sticker: BoardSticker) => void;
    onOpenTask: (taskId: number) => void;
    /** Начало перетаскивания стикера (по свободной области карточки). */
    onDragStart?: (event: ReactPointerEvent<HTMLElement>) => void;
    /** Начало изменения размера за уголок. */
    onResizeStart?: (event: ReactPointerEvent<HTMLElement>) => void;
}

const STICKER_TILTS = ["-0.35deg", "0.25deg", "-0.18deg", "0.32deg", "-0.28deg"];

/** Бумажный стикер на доске проекта: текст, связанные задачи, автор. */
export function BoardStickerCard({
    sticker,
    members,
    tasksById,
    readOnly,
    readOnlyHint,
    onEdit,
    onDelete,
    onOpenTask,
    onDragStart,
    onResizeStart,
}: BoardStickerCardProps) {
    const edited = sticker.updated_at !== sticker.created_at;
    // Лёгкий «бумажный» наклон, постоянный для стикера: раньше он зависел от цвета из палитры.
    const tilt = STICKER_TILTS[Math.abs(sticker.id) % STICKER_TILTS.length];

    return (
        <article
            className={cn("project-sticker-card", readOnly && "project-sticker-card--static")}
            style={{ "--sticker-surface": stickerSurface(sticker.color), "--sticker-tilt": tilt } as CSSProperties}
            aria-label={`Стикер: ${sticker.body.slice(0, 80)}`}
            title={readOnly ? readOnlyHint : onResizeStart ? "Перетащите стикер за свободную область; размер — за уголок справа внизу" : "Перетащите стикер; двойной клик — изменить, размер — за ручки выделения"}
            onPointerDown={readOnly ? undefined : onDragStart}
        >
            <div className="project-sticker-card__tape" aria-hidden="true" />
            <p className="project-sticker-card__body scrollbar-thin">{sticker.body}</p>

            {sticker.task_ids.length > 0 && (
                <div className="project-sticker-card__tasks" aria-label="Связанные задачи">
                    {sticker.task_ids.map((taskId) => {
                        const task = tasksById.get(taskId);
                        return (
                            <button
                                key={taskId}
                                type="button"
                                data-no-drag
                                className="project-sticker-task"
                                title={task ? `Открыть задачу ${task.key} · ${task.title}` : `Задача #${taskId}`}
                                onClick={() => onOpenTask(taskId)}
                            >
                                <Link2 size={11} aria-hidden="true" />
                                <span>{task?.key ?? `#${taskId}`}</span>
                            </button>
                        );
                    })}
                </div>
            )}

            <footer className="project-sticker-card__footer">
                <BoardStickerAuthor sticker={sticker} members={members} />
                <div className="project-sticker-card__meta">
                    <span title={formatDateTime(sticker.updated_at)}>
                        {formatDateTime(sticker.updated_at)}
                        {edited ? " · изм." : ""}
                    </span>
                    {!readOnly && (
                        <div className="project-sticker-card__actions" data-no-drag>
                            <IconButton size="sm" label="Изменить стикер" className="project-sticker-action" onClick={() => onEdit(sticker)}>
                                <Pencil size={13} aria-hidden="true" />
                            </IconButton>
                            <IconButton
                                size="sm"
                                label="Удалить стикер"
                                variant="destructive"
                                className="project-sticker-action project-sticker-action--danger"
                                onClick={() => onDelete(sticker)}
                            >
                                <Trash2 size={13} aria-hidden="true" />
                            </IconButton>
                        </div>
                    )}
                </div>
            </footer>
            {!readOnly && onResizeStart && (
                <span
                    className="project-sticker-card__resize"
                    data-no-drag
                    role="presentation"
                    title="Потяните, чтобы изменить размер"
                    onPointerDown={onResizeStart}
                >
                    ↘
                </span>
            )}
        </article>
    );
}
