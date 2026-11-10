import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, endpoints, queryKeys } from "@/lib/api";
import { fullName } from "@/lib/types";
import type { Project, ProjectStage, Task, TaskKind, TaskTransferPreview, TaskTransferResult } from "@/lib/types";
import {
    buildTransferTarget,
    TRANSFER_KIND_HINTS,
    TRANSFER_KIND_LABELS,
    transferLossesText,
    transferOptions,
    transferTargetQuery,
    type TransferKind,
} from "@/lib/taskTransfer";
import { useToast } from "@/lib/toast";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { Field, Select } from "@/components/ui/Field";
import { ErrorMessage, Skeleton } from "@/components/ui/States";

interface TransferTaskDialogProps {
    task: Task;
    kind: TaskKind;
    onClose: () => void;
    onMoved: (result: TaskTransferResult) => void;
}

const KINDS: TransferKind[] = ["PROJECT", "PERSONAL"];

/**
 * «Переместить задачу»: в другой проект (со стадией) или сделать разовой.
 * Перед подтверждением показывает, что изменится и что будет снято со старого места.
 */
export function TransferTaskDialog({ task, kind, onClose, onMoved }: TransferTaskDialogProps) {
    const queryClient = useQueryClient();
    const toast = useToast();
    const projectsQuery = useQuery({
        queryKey: queryKeys.projects,
        queryFn: () => api.get<Project[]>(endpoints.projects()),
    });
    const options = useMemo(
        () => transferOptions(task, kind, projectsQuery.data ?? []),
        [task, kind, projectsQuery.data],
    );
    const [moveKind, setTransferKind] = useState<TransferKind>(kind === "PROJECT" ? "PERSONAL" : "PROJECT");
    const [projectId, setProjectId] = useState<number | null>(null);
    const [stageId, setStageId] = useState<number | null>(null);
    const [removeOutsiders, setRemoveOutsiders] = useState(false);

    const stagesQuery = useQuery({
        queryKey: queryKeys.stages(projectId ?? 0),
        queryFn: () => api.get<ProjectStage[]>(endpoints.projectStages(projectId as number)),
        enabled: moveKind === "PROJECT" && projectId !== null,
    });
    const previewTarget = buildTransferTarget(moveKind, { projectId, stageId });
    const previewQuery = useQuery({
        queryKey: ["task-transfer-preview", task.id, previewTarget] as const,
        queryFn: () => api.get<TaskTransferPreview>(endpoints.taskTransferPreview(task.id, transferTargetQuery(previewTarget!))),
        enabled: previewTarget !== null,
        retry: false,
    });
    const preview = previewQuery.data;
    const outsiders = preview?.outsiders ?? [];
    const lossesText = preview ? transferLossesText(preview.losses) : "";

    const move = useMutation({
        mutationFn: () =>
            api.post<TaskTransferResult>(
                endpoints.taskTransfer(task.id),
                buildTransferTarget(moveKind, { projectId, stageId, removeOutsiders }),
            ),
        onSuccess: (result) => {
            queryClient.setQueryData(queryKeys.task(task.id), result.task);
            // Задача ушла со старого места и появилась в новом: обновляем всё, где она видна.
            for (const key of [queryKeys.projects, queryKeys.myTasks, queryKeys.dashboard]) {
                queryClient.invalidateQueries({ queryKey: key });
            }
            queryClient.invalidateQueries({ queryKey: queryKeys.taskActivity(task.id) });
            toast.success(`Задача перенесена: ${result.from_key} → ${result.to_key}`);
            onMoved(result);
            onClose();
        },
    });

    const chooseKind = (next: TransferKind) => {
        setTransferKind(next);
        setRemoveOutsiders(false);
        move.reset();
    };
    const available: Record<TransferKind, boolean> = {
        PROJECT: options.projects.length > 0,
        PERSONAL: options.personal,
    };
    const blockedByOutsiders = outsiders.length > 0 && !removeOutsiders;
    const canConfirm = previewTarget !== null && preview !== undefined && !blockedByOutsiders && !move.isPending;
    const confirmHint = previewTarget === null
        ? "Выберите, куда перенести задачу"
        : blockedByOutsiders
          ? "Подтвердите, что людей не из команды проекта нужно убрать из задачи"
          : "Перенести задачу — она получит новый номер, старый останется в истории";

    return (
        <Modal
            title="Переместить задачу"
            description={`${task.key} · ${task.title}`}
            size="md"
            tall
            isOpen
            isDismissable={!move.isPending}
            onOpenChange={(open) => !open && !move.isPending && onClose()}
            footer={
                <>
                    <Button disabled={move.isPending} onClick={onClose}>
                        Отмена
                    </Button>
                    <Button variant="primary" disabled={!canConfirm} title={confirmHint} onClick={() => move.mutate()}>
                        {move.isPending ? "Переносим…" : "Перенести"}
                    </Button>
                </>
            }
        >
            <div className="flex flex-col gap-4">
                <div className="flex gap-2 rounded-[var(--radius-control)] border border-line-subtle p-1 self-start" role="group" aria-label="Куда перенести">
                    {KINDS.map((item) => (
                        <button
                            key={item}
                            type="button"
                            aria-pressed={moveKind === item}
                            disabled={!available[item]}
                            title={available[item] ? TRANSFER_KIND_HINTS[item] : unavailableHint(item, kind)}
                            onClick={() => chooseKind(item)}
                        >
                            {TRANSFER_KIND_LABELS[item]}
                        </button>
                    ))}
                </div>
                <p className="text-[12px] text-muted">{TRANSFER_KIND_HINTS[moveKind]}</p>

                {moveKind === "PROJECT" && (
                    <div className="grid gap-3 sm:grid-cols-2">
                        <Field label="Проект" hint="Только проекты, где вы в команде">
                            {(id) => (
                                <Select
                                    id={id}
                                    value={projectId ?? ""}
                                    disabled={move.isPending}
                                    onChange={(event) => {
                                        setProjectId(event.target.value ? Number(event.target.value) : null);
                                        setStageId(null);
                                        setRemoveOutsiders(false);
                                    }}
                                >
                                    <option value="">Выберите проект…</option>
                                    {options.projects.map((project) => (
                                        <option key={project.id} value={project.id}>
                                            {project.key} · {project.name}
                                        </option>
                                    ))}
                                </Select>
                            )}
                        </Field>
                        <Field label="Стадия" hint="Пусто — по состоянию задачи: новая, в работе или готово">
                            {(id) => (
                                <Select
                                    id={id}
                                    value={stageId ?? ""}
                                    disabled={projectId === null || stagesQuery.isPending || move.isPending}
                                    onChange={(event) => setStageId(event.target.value ? Number(event.target.value) : null)}
                                >
                                    <option value="">По состоянию задачи</option>
                                    {stagesQuery.data?.map((stage) => (
                                        <option key={stage.id} value={stage.id}>
                                            {stage.name}
                                        </option>
                                    ))}
                                </Select>
                            )}
                        </Field>
                    </div>
                )}

                {previewTarget !== null && previewQuery.isPending && <Skeleton className="h-20 w-full" />}
                {previewQuery.error && (
                    <ErrorMessage title="Сюда перенести нельзя" message={(previewQuery.error as Error).message} />
                )}
                {preview && (
                    <section className="rounded-md border border-line-subtle bg-surface/35 flex flex-col gap-1.5 px-3 py-2.5 text-[13px]" aria-label="Что изменится">
                        <p>
                            <span className="font-mono">{preview.from_key}</span> станет{" "}
                            <span className="font-mono">{preview.target_key}-…</span> в «{preview.target_name}»
                        </p>
                        {preview.notes.map((note) => (
                            <p key={note}>{note}</p>
                        ))}
                        <p>Исполнитель: {preview.executor ? fullName(preview.executor) : "не назначен"}</p>
                        <p className="text-muted">
                            Комментарии, чек-лист, файлы, отчёт и история переедут вместе с задачей.
                        </p>
                    </section>
                )}
                {preview && lossesText && (
                    <p className="rounded-md border border-line-subtle bg-surface/35 border-warning/40 bg-warning/10 px-3 py-2 text-[13px]" role="note">
                        Будет снято со старого проекта: {lossesText}. Эти связи относятся к старому проекту и в новом месте
                        не нужны — в истории задачи останется запись.
                    </p>
                )}
                {preview && outsiders.length > 0 && (
                    <div className="rounded-md border border-line-subtle bg-surface/35 border-warning/40 bg-warning/10 flex flex-col gap-2 px-3 py-2 text-[13px]" role="note">
                        <p>
                            Не в команде проекта: {outsiders.map((user) => fullName(user)).join(", ")}. В задаче проекта могут
                            быть только люди из его команды — добавьте их в команду проекта или уберите из задачи.
                        </p>
                        <label className="flex items-center gap-2">
                            <input
                                type="checkbox"
                                checked={removeOutsiders}
                                onChange={(event) => setRemoveOutsiders(event.target.checked)}
                            />
                            Убрать их из задачи при переносе
                        </label>
                    </div>
                )}
                {move.error && <ErrorMessage title="Не удалось перенести" message={(move.error as Error).message} />}
            </div>
        </Modal>
    );
}

function unavailableHint(item: TransferKind, current: TaskKind): string {
    if (item === "PERSONAL" && current === "PERSONAL") return "Задача уже разовая";
    return item === "PROJECT"
        ? "Нет проектов, где вы в команде (кроме текущего)"
        : "Задача уже разовая";
}
