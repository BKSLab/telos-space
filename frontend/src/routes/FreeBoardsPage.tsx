import "@/components/board/board.css";
import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PenTool, Plus } from "lucide-react";
import { ApiError, api, endpoints, queryKeys } from "@/lib/api";
import { pendingBoardSaves } from "@/lib/board/saveQueue";
import type { BoardContent, Board } from "@/lib/board/types";
import { useToast } from "@/lib/toast";
import { BoardEditor } from "@/components/board/BoardEditor";
import { BoardTabs } from "@/components/board/BoardTabs";
import { Button } from "@/components/ui/Button";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";

const PAGE_HINT = "личные доски для схем и набросков без проекта — видите их только вы";

function BoardSkeleton() {
    return (
        <div role="status" aria-label="Загрузка доски" className="flex h-full flex-col gap-3 p-4">
            <Skeleton className="h-8 w-72" />
            <Skeleton className="min-h-0 flex-1" />
        </div>
    );
}

function nextBoardTitle(boards: Board[]): string {
    const taken = new Set(boards.map((board) => board.title));
    for (let index = boards.length + 1; ; index += 1) {
        const title = `Доска ${index}`;
        if (!taken.has(title)) return title;
    }
}

/**
 * Раздел «Доски»: личные доски без проекта (docs/boards-contract.md, «Свободные доски»).
 * Тот же редактор, что у проекта, но без задач и участников: стикеры не связываются
 * с задачами, доска видна только автору. Выбранная доска — в `?board=<id>`.
 */
export function FreeBoardsPage() {
    const queryClient = useQueryClient();
    const toast = useToast();
    const [searchParams, setSearchParams] = useSearchParams();
    const boardsKey = queryKeys.freeBoards;

    const boardsQuery = useQuery({
        queryKey: boardsKey,
        queryFn: () => api.get<Board[]>(endpoints.freeBoards()),
        retry: 1,
    });
    const boards = boardsQuery.data ?? [];
    const requestedId = Number(searchParams.get("board"));
    const activeBoard = boards.find((board) => board.id === requestedId) ?? boards[0] ?? null;
    const activeBoardId = activeBoard?.id;
    useEffect(() => {
        if (requestedId || !activeBoardId) return;
        setSearchParams(current => {
            const next = new URLSearchParams(current);
            next.set("board", String(activeBoardId));
            return next;
        }, { replace: true });
    }, [activeBoardId, requestedId, setSearchParams]);

    const contentQuery = useQuery({
        queryKey: queryKeys.freeBoardContent(activeBoard?.id ?? 0),
        queryFn: async () => {
            const boardId = activeBoard!.id;
            await pendingBoardSaves.get(boardId);
            return api.get<BoardContent>(endpoints.boardContent(null, boardId));
        },
        enabled: activeBoard !== null,
        // Сцена живёт в редакторе; фоновые перечитывания сбили бы работу посреди жеста.
        staleTime: Infinity,
        gcTime: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
        retry: 1,
    });

    function selectBoard(board: Board | null) {
        setSearchParams((current) => {
            const next = new URLSearchParams(current);
            if (board) next.set("board", String(board.id));
            else next.delete("board");
            return next;
        });
    }

    function conflictMessage(error: unknown, fallback: string): string {
        if (error instanceof ApiError && error.status === 409) {
            void queryClient.invalidateQueries({ queryKey: boardsKey });
            return error.message && !error.message.startsWith("{") ? error.message : fallback;
        }
        return error instanceof Error && error.message ? error.message : fallback;
    }

    const createMutation = useMutation({
        mutationFn: () => api.post<Board>(endpoints.freeBoards(), { title: nextBoardTitle(boards) }),
        onSuccess: (created) => {
            queryClient.setQueryData<Board[]>(boardsKey, (current = []) => [...current, created]);
            selectBoard(created);
        },
        onError: (error) => toast.error(`Доска не создана: ${conflictMessage(error, "ошибка сервера")}`),
    });

    const renameMutation = useMutation({
        mutationFn: ({ board, title }: { board: Board; title: string }) =>
            api.patch<Board>(endpoints.board(null, board.id), { title, revision: board.revision }),
        onSuccess: (updated) => {
            queryClient.setQueryData<Board[]>(boardsKey, (current = []) => current.map((board) => (board.id === updated.id ? updated : board)));
        },
        onError: (error) => toast.error(`Доска не переименована: ${conflictMessage(error, "её изменили в другой вкладке, список обновлён")}`),
    });

    const deleteMutation = useMutation({
        mutationFn: (board: Board) => api.delete<void>(`${endpoints.board(null, board.id)}?revision=${board.revision}`),
        onSuccess: (_, deleted) => {
            const rest = boards.filter((board) => board.id !== deleted.id);
            queryClient.setQueryData<Board[]>(boardsKey, rest);
            if (activeBoard?.id === deleted.id) selectBoard(rest[0] ?? null);
        },
    });

    const createButton = (
        <Button
            size="sm"
            variant="primary"
            icon={<Plus size={14} aria-hidden="true" />}
            disabled={createMutation.isPending}
            title="Создать личную доску: стикеры, фигуры и стрелки, как на доске проекта"
            onClick={() => createMutation.mutate()}
        >
            Новая доска
        </Button>
    );

    let body;
    if (boardsQuery.isPending) {
        body = <BoardSkeleton />;
    } else if (boardsQuery.error) {
        body = (
            <div className="p-4">
                <ErrorMessage
                    message={(boardsQuery.error as Error).message}
                    action={<Button size="sm" variant="ghost" onClick={() => void boardsQuery.refetch()}>Повторить</Button>}
                />
            </div>
        );
    } else if (!activeBoard) {
        body = (
            <div className="p-4 sm:p-5">
                <EmptyState
                    icon={<PenTool size={28} aria-hidden="true" />}
                    title="Личных досок пока нет"
                    description="Здесь можно нарисовать схему или набросать идеи без проекта и задач. Доску видите только вы."
                    action={createButton}
                />
            </div>
        );
    } else {
        body = (
            <>
                <BoardTabs
                    free
                    boards={boards}
                    activeId={activeBoard.id}
                    readOnly={false}
                    creating={createMutation.isPending}
                    onSelect={selectBoard}
                    onCreate={() => createMutation.mutate()}
                    onRename={(board, title) => renameMutation.mutateAsync({ board, title }).catch(() => undefined)}
                    onDelete={(board) =>
                        deleteMutation.mutateAsync(board).catch((error: unknown) => {
                            throw new Error(
                                error instanceof ApiError && error.status === 409
                                    ? "Доску не удалить: её только что изменили в другой вкладке. Обновите страницу."
                                    : error instanceof Error ? error.message : "Не удалось удалить доску.",
                            );
                        })
                    }
                />
                {contentQuery.isPending ? (
                    <BoardSkeleton />
                ) : contentQuery.error || !contentQuery.data ? (
                    <div className="p-4">
                        <ErrorMessage
                            message={(contentQuery.error as Error | null)?.message ?? "Сервер вернул пустой ответ."}
                            action={<Button size="sm" variant="ghost" onClick={() => void contentQuery.refetch()}>Повторить</Button>}
                        />
                    </div>
                ) : (
                    <BoardEditor
                        // Перезагрузка содержимого (после конфликта) пересоздаёт редактор с чистой историей.
                        key={`${activeBoard.id}:${contentQuery.dataUpdatedAt}`}
                        project={null}
                        board={activeBoard}
                        content={contentQuery.data}
                        members={[]}
                        tasks={[]}
                        tasksLoading={false}
                        tasksError={null}
                        readOnly={false}
                        onRetryTasks={() => undefined}
                        onReload={() => void contentQuery.refetch()}
                    />
                )}
            </>
        );
    }

    return (
        <section className="board-panel board-page h-full" aria-label="Мои доски">
            <div className="board-panel-heading">
                <h1 className="board-page-title">Доски</h1>
                <span className="board-page-hint board-hide-narrow">{PAGE_HINT}</span>
            </div>
            {body}
        </section>
    );
}
