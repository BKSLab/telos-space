import { useState } from "react";
import { MoreHorizontal, Pencil, Plus, Trash2 } from "lucide-react";
import { cn } from "@/lib/cn";
import type { Board } from "@/lib/board/types";
import { Popover } from "@/components/ui/Popover";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";

interface BoardTabsProps {
    boards: Board[];
    activeId: number;
    readOnly: boolean;
    creating: boolean;
    onSelect: (board: Board) => void;
    onCreate: () => void;
    onRename: (board: Board, title: string) => Promise<unknown>;
    onDelete: (board: Board) => Promise<unknown>;
    /** Личные доски без проекта: последнюю удалить можно, подписи — про «мои доски». */
    free?: boolean;
}

const LAST_BOARD_HINT = "Последнюю доску проекта удалить нельзя — сначала создайте другую";

/**
 * Вкладки досок над холстом. Двойной клик по названию — переименовать,
 * «⋯» у активной — переименовать или удалить (с подтверждением).
 */
export function BoardTabs({ boards, activeId, readOnly, creating, onSelect, onCreate, onRename, onDelete, free = false }: BoardTabsProps) {
    const [renamingId, setRenamingId] = useState<number | null>(null);
    const [draft, setDraft] = useState("");
    const [deleteTarget, setDeleteTarget] = useState<Board | null>(null);
    const [deleting, setDeleting] = useState(false);
    const [deleteError, setDeleteError] = useState<string | null>(null);
    const lastBoard = !free && boards.length <= 1;

    function startRename(board: Board) {
        if (readOnly) return;
        setDraft(board.title);
        setRenamingId(board.id);
    }

    function commitRename(board: Board) {
        const title = draft.trim().slice(0, 120);
        setRenamingId(null);
        if (!title || title === board.title) return;
        void onRename(board, title);
    }

    async function confirmDelete() {
        if (!deleteTarget) return;
        setDeleting(true);
        setDeleteError(null);
        try {
            await onDelete(deleteTarget);
            setDeleteTarget(null);
        } catch (error) {
            setDeleteError(error instanceof Error && error.message ? error.message : "Не удалось удалить доску.");
        } finally {
            setDeleting(false);
        }
    }

    return (
        <div className="board-tabs">
            <div className="board-tabs__list board-scroll" role="tablist" aria-label={free ? "Мои доски" : "Доски проекта"}>
                {boards.map((board) => {
                    const active = board.id === activeId;
                    if (renamingId === board.id) {
                        return (
                            <input
                                key={board.id}
                                className="board-tab__input board-input"
                                aria-label="Название доски"
                                autoFocus
                                maxLength={120}
                                value={draft}
                                onChange={(event) => setDraft(event.target.value)}
                                onFocus={(event) => event.target.select()}
                                onBlur={() => commitRename(board)}
                                onKeyDown={(event) => {
                                    if (event.key === "Enter") commitRename(board);
                                    if (event.key === "Escape") setRenamingId(null);
                                }}
                            />
                        );
                    }
                    return (
                        <span key={board.id} data-selected={active || undefined} className={cn("brand-tab board-tab", active && "board-tab--active")}>
                            <button
                                type="button"
                                role="tab"
                                aria-selected={active}
                                className="board-tab__title"
                                title={readOnly ? board.title : `${board.title} — двойной клик, чтобы переименовать`}
                                onClick={() => onSelect(board)}
                                onDoubleClick={() => startRename(board)}
                            >
                                {board.title}
                            </button>
                            {active && !readOnly && (
                                <span title="Переименовать или удалить эту доску">
                                <Popover
                                    label={`Действия с доской «${board.title}»`}
                                    triggerClassName="board-tab__menu"
                                    trigger={<MoreHorizontal size={14} aria-hidden="true" />}
                                    align="start"
                                    width={220}
                                >
                                    {({ close }) => (
                                        <div className="flex flex-col gap-1">
                                            <Button
                                                size="sm"
                                                variant="ghost"
                                                icon={<Pencil size={13} aria-hidden="true" />}
                                                onClick={() => {
                                                    close();
                                                    startRename(board);
                                                }}
                                            >
                                                Переименовать
                                            </Button>
                                            <Button
                                                size="sm"
                                                variant="ghost"
                                                icon={<Trash2 size={13} aria-hidden="true" />}
                                                disabled={lastBoard}
                                                title={lastBoard ? LAST_BOARD_HINT : "Удалить доску со всеми стикерами и фигурами"}
                                                onClick={() => {
                                                    close();
                                                    setDeleteError(null);
                                                    setDeleteTarget(board);
                                                }}
                                            >
                                                Удалить доску
                                            </Button>
                                            {lastBoard && <p className="px-2 text-[11px] text-muted">{LAST_BOARD_HINT}</p>}
                                        </div>
                                    )}
                                </Popover>
                                </span>
                            )}
                        </span>
                    );
                })}
            </div>
            {!readOnly && (
                <button
                    type="button"
                    className="board-tabs__add"
                    disabled={creating}
                    title={free ? "Новая личная доска — появится отдельной вкладкой, видите её только вы" : "Новая доска проекта — появится отдельной вкладкой"}
                    onClick={onCreate}
                >
                    <Plus size={14} aria-hidden="true" />
                    <span>доска</span>
                </button>
            )}

            {deleteTarget && (
                <Modal
                    isOpen
                    onOpenChange={(open) => {
                        if (!open && !deleting) setDeleteTarget(null);
                    }}
                    isDismissable={!deleting}
                    title={`Удалить доску «${deleteTarget.title}»?`}
                    description="Все стикеры и фигуры на ней будут удалены вместе с доской."
                    footer={
                        <>
                            <Button disabled={deleting} onClick={() => setDeleteTarget(null)}>Отмена</Button>
                            <Button variant="destructive" disabled={deleting} onClick={() => void confirmDelete()}>
                                {deleting ? "Удаление…" : "Удалить доску"}
                            </Button>
                        </>
                    }
                >
                    {deleteError && <p role="alert" className="text-[12px] text-danger">{deleteError}</p>}
                </Modal>
            )}
        </div>
    );
}
