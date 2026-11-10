import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, WandSparkles } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import { fileDraftKey } from "@/lib/files";
import type {
    DocumentListItem,
    ProjectMember,
    ProjectStage,
    Task,
    TaskDocumentImport,
    TaskPriority,
    TaskRephraseResult,
} from "@/lib/types";
import { PRIORITY_LABELS, PRIORITY_ORDER } from "@/lib/types";
import { useCurrentUser } from "@/lib/useAuth";
import { useToast } from "@/lib/toast";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { Field, Input, Select, Textarea } from "@/components/ui/Field";
import { ErrorMessage } from "@/components/ui/States";
import { AiOperationStatus } from "@/components/ui/AiOperationStatus";
import { TaskDateRangePicker } from "@/components/tasks/TaskDateRangePicker";
import { TaskDocumentsField } from "@/components/tasks/TaskDocumentsField";
import { TaskPeopleFields } from "@/components/tasks/TaskPeopleFields";
import { TaskChecklistField } from "@/components/tasks/TaskChecklistField";
import { checklistInput, isChecklistValid } from "@/lib/checklists";
import type { TaskChecklist } from "@/lib/checklists";

interface CreateTaskDialogProps {
    projectId: number;
    stages: ProjectStage[];
    /** Раздел ИСР, если задача создаётся прямо в структуре. */
    wbsNodeId?: number | null;
    initialDueDate?: string;
    isOpen: boolean;
    onClose: () => void;
    onCreated?: (task: Task) => void;
}

interface CreateTaskResult {
    task: Task;
    documentWarnings: string[];
}

export function CreateTaskDialog({
    projectId,
    stages,
    wbsNodeId = null,
    initialDueDate,
    isOpen,
    onClose,
    onCreated,
}: CreateTaskDialogProps) {
    const queryClient = useQueryClient();
    const toast = useToast();
    const [title, setTitle] = useState("");
    const [description, setDescription] = useState("");
    const [checklist, setChecklist] = useState<TaskChecklist | null>(null);
    const [checklistBusy, setChecklistBusy] = useState(false);
    const [priority, setPriority] = useState<TaskPriority>("MEDIUM");
    const [stageId, setStageId] = useState<string>("");
    const [startDate, setStartDate] = useState("");
    const [dueDate, setDueDate] = useState(initialDueDate ?? "");
    const [executorId, setExecutorId] = useState<number | null>(null);
    const [reporterId, setReporterId] = useState<number | null | undefined>(undefined);
    const [observerIds, setObserverIds] = useState<number[]>([]);
    const [selectedDocumentIds, setSelectedDocumentIds] = useState<number[]>([]);
    const [newDocumentFiles, setNewDocumentFiles] = useState<File[]>([]);
    const [fileCaptions, setFileCaptions] = useState<Record<string, string>>({});
    const [documentError, setDocumentError] = useState<string | null>(null);

    const currentUserQuery = useCurrentUser();
    const membersQuery = useQuery({
        queryKey: queryKeys.projectMembers(projectId),
        queryFn: () => api.get<ProjectMember[]>(endpoints.projectMembers(projectId)),
        enabled: isOpen,
    });
    const documentsQuery = useQuery({
        queryKey: queryKeys.documents(projectId),
        queryFn: () => api.get<DocumentListItem[]>(endpoints.projectDocuments(projectId)),
        enabled: isOpen,
    });

    const defaultStageId = stages.length > 0 ? String(stages[0].id) : "";
    const selectedStageId = stageId || defaultStageId;
    const currentMember = membersQuery.data?.find(
        (member) => member.user.id === currentUserQuery.data?.id,
    );
    const selectedReporterId =
        reporterId === undefined ? (currentMember?.user.id ?? null) : reporterId;
    const canChangeReporter = currentMember?.role === "OWNER";

    function reset() {
        setTitle("");
        setDescription("");
        setChecklist(null);
        setPriority("MEDIUM");
        setStageId("");
        setStartDate("");
        setDueDate(initialDueDate ?? "");
        setExecutorId(null);
        setReporterId(undefined);
        setObserverIds([]);
        setSelectedDocumentIds([]);
        setNewDocumentFiles([]);
        setFileCaptions({});
        setDocumentError(null);
    }

    const createMutation = useMutation({
        mutationFn: async (): Promise<CreateTaskResult> => {
            const task = await api.post<Task>(endpoints.projectTasks(projectId), {
                title: title.trim(),
                description_md: description.trim() || null,
                checklist: checklistInput(checklist),
                priority,
                stage_id: selectedStageId === "" ? null : Number(selectedStageId),
                wbs_node_id: wbsNodeId,
                start_date: startDate || null,
                due_date: dueDate || null,
                executor_id: executorId,
                reporter_id: selectedReporterId,
                observer_ids: observerIds,
            });

            const documentWarnings: string[] = [];
            for (const documentId of selectedDocumentIds) {
                try {
                    await api.post(endpoints.links(), {
                        document_id: documentId,
                        task_id: task.id,
                    });
                } catch (error) {
                    documentWarnings.push((error as Error).message);
                }
            }
            for (const file of newDocumentFiles) {
                try {
                    const body = new FormData();
                    body.append("file", file);
                    const caption = fileCaptions[fileDraftKey(file)]?.trim();
                    if (caption) body.append("caption", caption);
                    await api.postForm<TaskDocumentImport>(
                        endpoints.taskDocumentImport(task.id),
                        body,
                    );
                } catch (error) {
                    documentWarnings.push(`«${file.name}»: ${(error as Error).message}`);
                }
            }
            return { task, documentWarnings };
        },
        onSuccess: ({ task, documentWarnings }) => {
            queryClient.invalidateQueries({ queryKey: ["projects", projectId] });
            queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
            queryClient.invalidateQueries({ queryKey: ["projects", projectId, "documents"] });
            if (documentWarnings.length > 0) {
                toast.error(
                    `Задача создана, но не все документы добавлены: ${documentWarnings.join(" ")}`,
                );
            } else {
                toast.success("Задача создана");
            }
            reset();
            onCreated?.(task);
            onClose();
        },
    });

    const rephraseMutation = useMutation({
        mutationFn: async () => {
            const body = new FormData();
            body.append(
                "payload",
                JSON.stringify({
                    title: title.trim(),
                    description_md: description.trim(),
                    document_ids: selectedDocumentIds,
                    ...(isChecklistValid(checklist) ? { checklist: checklistInput(checklist) } : {}),
                }),
            );
            for (const file of newDocumentFiles) {
                body.append("files", file);
            }
            return api.postForm<TaskRephraseResult>(endpoints.taskRephrase(projectId), body);
        },
        onSuccess: (result) => setDescription(result.description_md),
    });

    const isBusy = createMutation.isPending || rephraseMutation.isPending || checklistBusy;

    return (
        <Modal
            title="Новая задача"
            description="Основные параметры, команда и рабочий контекст"
            size="lg"
            tall
            isOpen={isOpen}
            isDismissable={!createMutation.isPending}
            onOpenChange={(open) => {
                if (!open && !createMutation.isPending) {
                    onClose();
                }
            }}
            footer={
                <>
                    <Button disabled={createMutation.isPending} onClick={onClose}>
                        Отмена
                    </Button>
                    <Button
                        variant="primary"
                        disabled={
                            title.trim() === "" ||
                            checklistBusy || !isChecklistValid(checklist) ||
                            createMutation.isPending ||
                            rephraseMutation.isPending ||
                            membersQuery.isPending
                        }
                        onClick={() => createMutation.mutate()}
                    >
                        {createMutation.isPending ? "Создание…" : "Создать задачу"}
                    </Button>
                </>
            }
        >
            <div className="flex flex-col gap-4">
                {createMutation.error && (
                    <ErrorMessage
                        title="Не удалось создать задачу"
                        message={(createMutation.error as Error).message}
                    />
                )}
                {membersQuery.error && (
                    <ErrorMessage
                        title="Не удалось загрузить команду"
                        message={(membersQuery.error as Error).message}
                    />
                )}

                <Field label="Название">
                    {(id) => (
                        <Input
                            id={id}
                            autoFocus
                            value={title}
                            disabled={createMutation.isPending}
                            placeholder="Что нужно сделать"
                            onChange={(event) => setTitle(event.target.value)}
                        />
                    )}
                </Field>

                <div className="flex flex-col gap-1.5">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <label htmlFor="new-task-description" className="text-[11px] font-medium text-secondary">
                            Описание <span className="font-normal text-muted">· Markdown</span>
                        </label>
                        <Button
                            size="sm"
                            variant="ai"
                            icon={
                                rephraseMutation.isPending ? (
                                    <LoaderCircle
                                        size={13}
                                        className="animate-spin motion-reduce:animate-none"
                                        aria-hidden="true"
                                    />
                                ) : (
                                    <WandSparkles size={13} aria-hidden="true" />
                                )
                            }
                            disabled={description.trim() === "" || isBusy}
                            onClick={() => rephraseMutation.mutate()}
                        >
                            {rephraseMutation.isPending ? "Переформулируем…" : "Переформулировать"}
                        </Button>
                    </div>
                    {rephraseMutation.isPending && <AiOperationStatus message="Арис переформулирует описание задачи…"
                        description="Учитывает проект и выбранные материалы." />}
                    <Textarea
                        id="new-task-description"
                        rows={6}
                        value={description}
                        disabled={isBusy}
                        placeholder="Опишите результат, ограничения и важный контекст задачи"
                        onChange={(event) => {
                            setDescription(event.target.value);
                            rephraseMutation.reset();
                        }}
                    />
                    {rephraseMutation.error ? (
                        <p role="alert" className="text-[11px] text-danger">
                            {(rephraseMutation.error as Error).message} Исходный текст не изменён.
                        </p>
                    ) : (
                        <p className="text-[10px] text-muted">
                            AI учитывает проект, формулировки других задач и выбранные документы.
                        </p>
                    )}
                </div>

                <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Стадия">
                        {(id) => (
                            <Select
                                id={id}
                                value={selectedStageId}
                                disabled={isBusy}
                                onChange={(event) => setStageId(event.target.value)}
                            >
                                {stages.map((stage) => (
                                    <option key={stage.id} value={stage.id}>
                                        {stage.name}
                                    </option>
                                ))}
                            </Select>
                        )}
                    </Field>

                    <Field label="Приоритет">
                        {(id) => (
                            <Select
                                id={id}
                                value={priority}
                                disabled={isBusy}
                                onChange={(event) =>
                                    setPriority(event.target.value as TaskPriority)
                                }
                            >
                                {PRIORITY_ORDER.map((item) => (
                                    <option key={item} value={item}>
                                        {PRIORITY_LABELS[item]}
                                    </option>
                                ))}
                            </Select>
                        )}
                    </Field>

                    <Field
                        label="Период"
                        hint="Одна дата означает задачу на один день"
                        className="sm:col-span-2"
                    >
                        {() => (
                            <TaskDateRangePicker
                                startDate={startDate}
                                dueDate={dueDate}
                                disabled={isBusy}
                                onChange={(value) => {
                                    setStartDate(value.startDate);
                                    setDueDate(value.dueDate);
                                }}
                            />
                        )}
                    </Field>
                </div>

                <TaskChecklistField projectId={projectId} title={title} description={description}
                    documentIds={selectedDocumentIds} files={newDocumentFiles}
                    value={checklist} onChange={setChecklist} onBusyChange={setChecklistBusy}
                    disabled={createMutation.isPending || rephraseMutation.isPending} />

                <section className="flex flex-col gap-3" aria-labelledby="task-people-heading">
                    <p
                        id="task-people-heading"
                        className="text-[10px] font-semibold tracking-[0.12em] text-muted uppercase"
                    >
                        Участники задачи
                    </p>
                    <TaskPeopleFields
                        members={membersQuery.data ?? []}
                        executorId={executorId}
                        reporterId={selectedReporterId}
                        observerIds={observerIds}
                        canChangeReporter={canChangeReporter}
                        disabled={membersQuery.isPending || isBusy}
                        onExecutorChange={setExecutorId}
                        onReporterChange={setReporterId}
                        onObserversChange={setObserverIds}
                    />
                </section>

                <section className="flex flex-col gap-3" aria-labelledby="task-documents-heading">
                    <p
                        id="task-documents-heading"
                        className="text-[10px] font-semibold tracking-[0.12em] text-muted uppercase"
                    >
                        Документы
                    </p>
                    {documentsQuery.error && (
                        <ErrorMessage
                            title="Не удалось загрузить документы"
                            message={(documentsQuery.error as Error).message}
                        />
                    )}
                    <TaskDocumentsField
                        documents={documentsQuery.data ?? []}
                        selectedDocumentIds={selectedDocumentIds}
                        files={newDocumentFiles}
                        captions={fileCaptions}
                        disabled={isBusy}
                        loading={documentsQuery.isPending}
                        error={documentError}
                        onSelectedDocumentIdsChange={setSelectedDocumentIds}
                        onFilesChange={setNewDocumentFiles}
                        onCaptionsChange={setFileCaptions}
                        onError={setDocumentError}
                    />
                </section>
            </div>
        </Modal>
    );
}
