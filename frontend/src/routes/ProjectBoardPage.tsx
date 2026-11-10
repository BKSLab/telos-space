import "@/components/board/board.css";
import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, endpoints, queryKeys } from "@/lib/api";
import { pendingBoardSaves } from "@/lib/board/saveQueue";
import type { BoardContent, Board } from "@/lib/board/types";
import { useToast } from "@/lib/toast";
import type { ProjectMember, Task } from "@/lib/types";
import { useProjectOutlet } from "@/lib/useProjectOutlet";
import { BoardEditor } from "@/components/board/BoardEditor";
import { BoardTabs } from "@/components/board/BoardTabs";
import { Button } from "@/components/ui/Button";
import { ErrorMessage, Skeleton } from "@/components/ui/States";

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
 * Доски проекта: вкладки над холстом, выбранная доска — в `?board=<id>`
 * (без параметра — первая). Содержимое доски грузится одним запросом /content.
 */
export function ProjectBoardPage() {
    const project = useProjectOutlet();
    const queryClient = useQueryClient();
    const toast = useToast();
    const [searchParams, setSearchParams] = useSearchParams();
    const boardsKey = queryKeys.projectBoards(project.id);
    const readOnly = false;

    const boardsQuery = useQuery({
        queryKey: boardsKey,
        queryFn: () => api.get<Board[]>(endpoints.projectBoards(project.id)),
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
        queryKey: queryKeys.boardContent(project.id, activeBoard?.id ?? 0),
        queryFn: async () => {
            const boardId = activeBoard!.id;
            // Ушли с доски и вернулись, пока досылалось сохранение, — ждём его, чтобы не прочитать старое.
            await pendingBoardSaves.get(boardId);
            return api.get<BoardContent>(endpoints.boardContent(project.id, boardId));
        },
        enabled: activeBoard !== null,
        // Сцена живёт в редакторе; фоновые перечитывания сбили бы работу посреди жеста.
        staleTime: Infinity,
        gcTime: 0,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
        retry: 1,
    });
    const membersQuery = useQuery({
        queryKey: queryKeys.projectMembers(project.id),
        queryFn: () => api.get<ProjectMember[]>(endpoints.projectMembers(project.id)),
        retry: 1,
    });
    const tasksQuery = useQuery({
        queryKey: queryKeys.tasks(project.id),
        queryFn: () => api.get<Task[]>(endpoints.projectTasks(project.id)),
        retry: 1,
    });

    function selectBoard(board: Board) {
        setSearchParams((current) => {
            const next = new URLSearchParams(current);
            next.set("board", String(board.id));
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
        mutationFn: () => api.post<Board>(endpoints.projectBoards(project.id), { title: nextBoardTitle(boards) }),
        onSuccess: (created) => {
            queryClient.setQueryData<Board[]>(boardsKey, (current = []) => [...current, created]);
            selectBoard(created);
        },
        onError: (error) => toast.error(`Доска не создана: ${conflictMessage(error, "ошибка сервера")}`),
    });

    const renameMutation = useMutation({
        mutationFn: ({ board, title }: { board: Board; title: string }) =>
            api.patch<Board>(endpoints.board(project.id, board.id), { title, revision: board.revision }),
        onSuccess: (updated) => {
            queryClient.setQueryData<Board[]>(boardsKey, (current = []) => current.map((board) => (board.id === updated.id ? updated : board)));
        },
        onError: (error) => toast.error(`Доска не переименована: ${conflictMessage(error, "её уже изменил другой участник, список обновлён")}`),
    });

    const deleteMutation = useMutation({
        mutationFn: (board: Board) => api.delete<void>(`${endpoints.board(project.id, board.id)}?revision=${board.revision}`),
        onSuccess: (_, deleted) => {
            const rest = boards.filter((board) => board.id !== deleted.id);
            queryClient.setQueryData<Board[]>(boardsKey, rest);
            if (activeBoard?.id === deleted.id && rest[0]) selectBoard(rest[0]);
        },
    });

    if (boardsQuery.isPending || membersQuery.isPending) return <BoardSkeleton />;

    const loadingError = boardsQuery.error ?? membersQuery.error;
    if (loadingError || !activeBoard || !membersQuery.data) {
        return (
            <div className="board-panel h-full p-4 sm:p-5">
                <ErrorMessage
                    message={(loadingError as Error | null)?.message ?? "Сервер не вернул ни одной доски."}
                    action={
                        <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => {
                                void boardsQuery.refetch();
                                void membersQuery.refetch();
                            }}
                        >
                            Повторить
                        </Button>
                    }
                />
            </div>
        );
    }

    return (
        <section className="board-panel board-page h-full" aria-label="Доски проекта">
            <BoardTabs
                boards={boards}
                activeId={activeBoard.id}
                readOnly={readOnly}
                creating={createMutation.isPending}
                onSelect={selectBoard}
                onCreate={() => createMutation.mutate()}
                onRename={(board, title) => renameMutation.mutateAsync({ board, title }).catch(() => undefined)}
                onDelete={(board) =>
                    deleteMutation.mutateAsync(board).catch((error: unknown) => {
                        throw new Error(
                            error instanceof ApiError && error.status === 409
                                ? "Доску не удалить: это последняя доска проекта или её только что изменили. Обновите страницу."
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
                    project={project}
                    board={activeBoard}
                    content={contentQuery.data}
                    members={membersQuery.data}
                    tasks={tasksQuery.data ?? []}
                    tasksLoading={tasksQuery.isPending}
                    tasksError={(tasksQuery.error as Error | null) ?? null}
                    readOnly={readOnly}

                    onRetryTasks={() => void tasksQuery.refetch()}
                    onReload={() => void contentQuery.refetch()}
                />
            )}
        </section>
    );
}
