import { type CSSProperties, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search, X } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import { cn } from "@/lib/cn";
import {
    limitStickerTaskResults,
    mergeStickerTaskResults,
    normalizeStickerInput,
    searchStickerTasks,
    DEFAULT_STICKER_COLOR,
    normalizeStickerColor,
    STICKER_COLOR_PRESETS,
    STICKER_TASK_RESULTS_LIMIT,
    stickerSurface,
    stickerHasChanges,
    type BoardSticker,
    type BoardStickerInput,
    STICKER_SIZE_PRESETS,
    type BoardStickerSize,
} from "@/lib/board/stickers";
import type { Task } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { Field, Input, Textarea } from "@/components/ui/Field";
import { Skeleton } from "@/components/ui/States";
import { Modal } from "@/components/ui/Modal";

const MAX_BODY_LENGTH = 2000;
const MAX_LINKED_TASKS = 20;

interface BoardStickerDialogProps {
    /** null — свободная доска: проекта нет, связь с задачами недоступна. */
    projectId: number | null;
    sticker: BoardSticker | null;
    tasks: Task[];
    tasksLoading: boolean;
    isSaving: boolean;
    error: string | null;
    onClose: () => void;
    onSave: (input: BoardStickerInput) => void;
}

export function BoardStickerDialog({
    projectId,
    sticker,
    tasks,
    tasksLoading,
    isSaving,
    error,
    onClose,
    onSave,
}: BoardStickerDialogProps) {
    const [body, setBody] = useState(sticker?.body ?? "");
    const [color, setColor] = useState<string>(normalizeStickerColor(sticker?.color ?? DEFAULT_STICKER_COLOR));
    const colorIsPreset = STICKER_COLOR_PRESETS.some((option) => option.value === color);
    const [taskIds, setTaskIds] = useState<number[]>(sticker?.task_ids ?? []);
    const initialSize: BoardStickerSize = sticker && (sticker.width ?? 230) <= 200 ? "small" : sticker && (sticker.width ?? 230) >= 300 ? "large" : "medium";
    const [size, setSize] = useState<BoardStickerSize>(initialSize);
    const dimensions = sticker ? {} : STICKER_SIZE_PRESETS[size];
    const [taskSearch, setTaskSearch] = useState("");
    const requestedTaskSearch = taskSearch.trim();
    const [debouncedTaskSearch, setDebouncedTaskSearch] = useState("");

    useEffect(() => {
        const timeoutId = window.setTimeout(() => {
            setDebouncedTaskSearch(requestedTaskSearch);
        }, 250);
        return () => window.clearTimeout(timeoutId);
    }, [requestedTaskSearch]);

    const serverSearchEnabled = debouncedTaskSearch.length >= 3;
    const taskSearchQuery = useQuery({
        queryKey: queryKeys.tasks(projectId ?? 0, debouncedTaskSearch),
        queryFn: () => api.get<Task[]>(
            `${endpoints.projectTasks(projectId!)}?search=${encodeURIComponent(debouncedTaskSearch)}`,
        ),
        enabled: serverSearchEnabled && projectId !== null,
        retry: 1,
    });
    const normalized = normalizeStickerInput({ body, color, task_ids: taskIds, ...dimensions });
    const canSubmit = normalized.body.length > 0
        && normalized.body.length <= MAX_BODY_LENGTH
        && (!sticker || stickerHasChanges(sticker, normalized));
    const searchIsActive = requestedTaskSearch !== "";
    const localResults = useMemo(
        () => searchStickerTasks(tasks, requestedTaskSearch),
        [requestedTaskSearch, tasks],
    );
    const serverResultsReady = serverSearchEnabled
        && debouncedTaskSearch === requestedTaskSearch;
    const resultTasks = searchIsActive
        ? mergeStickerTaskResults(
            localResults,
            serverResultsReady ? taskSearchQuery.data ?? [] : [],
        )
        : tasks;
    const visibleTasks = limitStickerTaskResults(resultTasks);
    const waitingForServer = requestedTaskSearch.length >= 3
        && (!serverResultsReady || taskSearchQuery.isPending);
    const resultsLoading = searchIsActive
        ? localResults.length === 0 && (tasksLoading || waitingForServer)
        : tasksLoading;
    const resultsError = searchIsActive
        && localResults.length === 0
        && serverResultsReady
        ? taskSearchQuery.error
        : null;
    const knownTasksById = useMemo(
        () => new Map(
            [...tasks, ...(taskSearchQuery.data ?? [])].map((task) => [task.id, task]),
        ),
        [taskSearchQuery.data, tasks],
    );

    function toggleTask(taskId: number) {
        setTaskIds((current) => current.includes(taskId)
            ? current.filter((item) => item !== taskId)
            : current.length < MAX_LINKED_TASKS
                ? [...current, taskId]
                : current,
        );
    }

    return (
        <Modal
            isOpen
            onOpenChange={(open) => {
                if (!open && !isSaving) onClose();
            }}
            isDismissable={!isSaving}
            size="md"
            title={sticker ? "Изменить стикер" : "Новый стикер"}
            description="Короткая общая заметка для участников проекта"
            footer={
                <>
                    <Button onClick={onClose} disabled={isSaving}>Отмена</Button>
                    <Button
                        type="submit"
                        form="project-sticker-form"
                        variant="primary"
                        disabled={!canSubmit || isSaving}
                    >
                        {isSaving ? "Сохранение…" : sticker ? "Сохранить" : "Добавить"}
                    </Button>
                </>
            }
        >
            <form
                id="project-sticker-form"
                className="flex flex-col gap-5"
                onSubmit={(event) => {
                    event.preventDefault();
                    if (canSubmit && !isSaving) onSave(normalized);
                }}
            >
                <Field
                    label="Текст"
                    error={body.length > MAX_BODY_LENGTH ? "Не более 2000 символов." : undefined}
                    hint={`${body.length} / ${MAX_BODY_LENGTH}`}
                >
                    {(id) => (
                        <Textarea
                            id={id}
                            value={body}
                            rows={7}
                            maxLength={MAX_BODY_LENGTH}
                            autoFocus
                            required
                            placeholder="Что важно зафиксировать для команды?"
                            onChange={(event) => setBody(event.target.value)}
                        />
                    )}
                </Field>

                <fieldset className="flex flex-col gap-2">
                    <legend className="text-[11px] font-medium text-secondary">Цвет</legend>
                    <div className="flex flex-wrap items-center gap-2">
                        {STICKER_COLOR_PRESETS.map((option) => (
                            <button
                                key={option.value}
                                type="button"
                                aria-pressed={color === option.value}
                                aria-label={option.label}
                                title={option.label}
                                className="project-sticker-color"
                                style={{ "--sticker-choice": stickerSurface(option.value) } as CSSProperties}
                                onClick={() => setColor(option.value)}
                            >
                                <span aria-hidden="true" />
                            </button>
                        ))}
                        <label
                            className={cn("project-sticker-color-custom", !colorIsPreset && "project-sticker-color-custom--active")}
                            title="Любой цвет: нажмите, чтобы открыть палитру"
                        >
                            <input
                                type="color"
                                aria-label="Свой цвет стикера"
                                value={color}
                                onChange={(event) => setColor(normalizeStickerColor(event.target.value))}
                            />
                            {colorIsPreset ? "Свой цвет" : <code>{color}</code>}
                        </label>
                    </div>
                    <p className="text-[11px] text-muted">
                        На доске цвет смягчается под светлую или тёмную тему, чтобы текст на стикере читался.
                    </p>
                </fieldset>

                {!sticker && <fieldset className="flex flex-col gap-2">
                    <legend className="text-[11px] font-medium text-secondary">Размер</legend>
                    <div className="flex flex-wrap gap-2">
                        {(Object.entries(STICKER_SIZE_PRESETS) as [BoardStickerSize, typeof STICKER_SIZE_PRESETS.medium][]).map(([value, option]) => (
                            <button key={value} type="button" aria-pressed={size === value}
                                className={cn("project-sticker-size", size === value && "project-sticker-size--active")}
                                onClick={() => setSize(value)}>
                                {option.label}<span>{option.width}×{option.height}</span>
                            </button>
                        ))}
                    </div>
                    <p className="text-[11px] text-muted">После размещения размер можно изменить прямо на холсте.</p>
                </fieldset>}

                {projectId === null ? (
                    <p className="text-[11px] text-muted">
                        Это личная доска без проекта, поэтому стикер нельзя связать с задачами. Связи доступны на досках внутри проекта.
                    </p>
                ) : <fieldset className="flex min-h-0 flex-col gap-2">
                    <legend className="text-[11px] font-medium text-secondary">
                        Связанные задачи · {taskIds.length}/{MAX_LINKED_TASKS}
                    </legend>
                    {taskIds.length > 0 && (
                        <div
                            role="group"
                            className="project-sticker-selected-tasks scrollbar-thin"
                            aria-label="Выбранные задачи"
                        >
                            {taskIds.map((taskId) => {
                                const task = knownTasksById.get(taskId);
                                const removeLabel = task
                                    ? `Убрать связь с ${task.key} · ${task.title}`
                                    : `Убрать связь с задачей #${taskId}`;
                                return (
                                    <button
                                        key={taskId}
                                        type="button"
                                        className="project-sticker-selected-task"
                                        aria-label={removeLabel}
                                        title={removeLabel}
                                        onClick={() => toggleTask(taskId)}
                                    >
                                        <span>{task?.key ?? `#${taskId}`}</span>
                                        <X size={11} aria-hidden="true" />
                                    </button>
                                );
                            })}
                        </div>
                    )}
                    <div className="relative">
                        <Search
                            size={14}
                            aria-hidden="true"
                            className="pointer-events-none absolute top-2 left-2.5 text-muted"
                        />
                        <Input
                            type="search"
                            aria-label="Поиск задачи"
                            value={taskSearch}
                            maxLength={200}
                            placeholder="Ключ, название, описание или комментарий"
                            className="pl-8"
                            onChange={(event) => setTaskSearch(event.target.value)}
                        />
                    </div>
                    <div className="project-sticker-task-picker scrollbar-thin">
                        {resultsLoading && (
                            <div role="status" aria-label={searchIsActive ? "Поиск задач" : "Загрузка задач"}><Skeleton className="h-10 w-full" /></div>
                        )}
                        {!resultsLoading && resultsError && (
                            <div className="flex items-center justify-between gap-3 px-3 py-3">
                                <p role="alert" className="text-xs text-danger">
                                    Не удалось выполнить поиск.
                                </p>
                                <Button
                                    size="sm"
                                    variant="ghost"
                                    onClick={() => void taskSearchQuery.refetch()}
                                >
                                    Повторить
                                </Button>
                            </div>
                        )}
                        {!resultsLoading
                            && !resultsError
                            && !searchIsActive
                            && tasks.length === 0 && (
                                <p className="px-3 py-4 text-xs text-muted">
                                    В проекте пока нет задач для связи.
                                </p>
                            )}
                        {!resultsLoading
                            && !resultsError
                            && searchIsActive
                            && visibleTasks.length === 0 && (
                                <p className="px-3 py-4 text-xs text-muted">
                                    Задачи не найдены.
                                </p>
                            )}
                        {!resultsLoading
                            && !resultsError
                            && visibleTasks.map((task) => {
                                const selected = taskIds.includes(task.id);
                                const disabled = !selected && taskIds.length >= MAX_LINKED_TASKS;
                                return (
                                    <label
                                        key={task.id}
                                        className={cn(
                                            "project-sticker-task-option",
                                            selected && "project-sticker-task-option--selected",
                                            disabled && "opacity-45",
                                        )}
                                    >
                                        <input
                                            type="checkbox"
                                            checked={selected}
                                            disabled={disabled}
                                            onChange={() => toggleTask(task.id)}
                                        />
                                        <span className="shrink-0 font-mono text-[11px] text-accent">
                                            {task.key}
                                        </span>
                                        <span className="min-w-0 truncate text-[12px] text-secondary">
                                            {task.title}
                                        </span>
                                    </label>
                                );
                            })}
                    </div>
                    {!resultsLoading
                        && !resultsError
                        && resultTasks.length > STICKER_TASK_RESULTS_LIMIT && (
                            <p className="text-[10px] text-muted">
                                Показаны первые {STICKER_TASK_RESULTS_LIMIT} из {resultTasks.length}.
                                Уточните поиск, чтобы сузить список.
                            </p>
                        )}
                </fieldset>}

                {error && <p role="alert" className="text-[12px] text-danger">{error}</p>}
            </form>
        </Modal>
    );
}
