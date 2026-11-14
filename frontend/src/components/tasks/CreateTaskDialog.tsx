import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, WandSparkles } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { FileUploadDraft } from "@/lib/files";
import { fileDraftKey } from "@/lib/files";
import { newUuid } from "@/lib/uuid";
import { createMarkdownDocument, uploadDocument, type MarkdownDocumentDraft } from "@/lib/documents";
import type {
    DocumentListItem,
    ProjectMember,
    ProjectStage,
    Task,
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
    failedDocuments: DocumentListItem[];
    failedFiles: FileUploadDraft[];
    failedMarkdown: MarkdownDocumentDraft[];
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
    const [selectedDocuments, setSelectedDocuments] = useState<DocumentListItem[]>([]);
    const [newDocumentFiles, setNewDocumentFiles] = useState<FileUploadDraft[]>([]);
    const [markdownDocuments, setMarkdownDocuments] = useState<MarkdownDocumentDraft[]>([]);
    const [createdTask, setCreatedTask] = useState<Task | null>(null);
    const [documentWarnings, setDocumentWarnings] = useState<string[]>([]);
    const documentRequests = useRef(new Map<string, string>());
    const selectedDocumentRefs = selectedDocuments.map(document => document.ref);

    const currentUserQuery = useCurrentUser();
    const membersQuery = useQuery({
        queryKey: queryKeys.projectMembers(projectId),
        queryFn: () => api.get<ProjectMember[]>(endpoints.projectMembers(projectId)),
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

    function closeDialog() {
        if (createdTask) { onCreated?.(createdTask); reset(); }
        onClose();
    }

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
        setSelectedDocuments([]);
        setNewDocumentFiles([]);
        setMarkdownDocuments([]);
        setCreatedTask(null);
        setDocumentWarnings([]);
        documentRequests.current.clear();
    }

    const createMutation = useMutation({
        mutationFn: async (): Promise<CreateTaskResult> => {
            const task = createdTask ?? await api.post<Task>(endpoints.projectTasks(projectId), {
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
            setCreatedTask(task);
            const documentWarnings: string[] = [];
            const failedDocuments: DocumentListItem[] = [];
            const failedFiles: FileUploadDraft[] = [];
            const failedMarkdown: MarkdownDocumentDraft[] = [];
            for (const document of selectedDocuments) {
                try {
                    await api.post(endpoints.documentTask(document.ref, task.id));
                } catch (error) {
                    documentWarnings.push((error as Error).message);
                    failedDocuments.push(document);
                }
            }
            for (const draft of markdownDocuments) {
                try {
                    await createMarkdownDocument({ taskId: task.id }, draft, draft.key);
                } catch (error) {
                    documentWarnings.push(`«${draft.title}»: ${(error as Error).message}`);
                    failedMarkdown.push(draft);
                }
            }
            for (const draft of newDocumentFiles) {
                try {
                    const signature = `${fileDraftKey(draft.file)}:${draft.caption}`;
                    if (!documentRequests.current.has(signature)) documentRequests.current.set(signature, newUuid());
                    await uploadDocument({ taskId: task.id }, draft.file, draft.caption, documentRequests.current.get(signature));
                } catch (error) {
                    documentWarnings.push(`«${draft.file.name}»: ${(error as Error).message}`);
                    failedFiles.push(draft);
                }
            }
            return { task, documentWarnings, failedDocuments, failedFiles, failedMarkdown };
        },
        onSuccess: ({ task, documentWarnings, failedDocuments, failedFiles, failedMarkdown }) => {
            queryClient.invalidateQueries({ queryKey: ["projects", projectId] });
            queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
            queryClient.invalidateQueries({ queryKey: ["documents"] });
            if (documentWarnings.length > 0) {
                setDocumentWarnings(documentWarnings);
                setSelectedDocuments(failedDocuments);
                setNewDocumentFiles(failedFiles);
                setMarkdownDocuments(failedMarkdown);
                toast.error(
                    `Задача создана, но не все документы добавлены: ${documentWarnings.join(" ")}`,
                );
                return;
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
                    document_refs: selectedDocumentRefs,
                    ...(isChecklistValid(checklist) ? { checklist: checklistInput(checklist) } : {}),
                }),
            );
            for (const draft of newDocumentFiles) {
                body.append("files", draft.file);
            }
            for (const draft of markdownDocuments) {
                body.append("files", new File([draft.content_md], `${draft.title}.md`, { type: "text/markdown" }));
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
                    closeDialog();
                }
            }}
            footer={
                <>
                    <Button disabled={createMutation.isPending} onClick={closeDialog}>
                        {createdTask ? "Закрыть" : "Отмена"}
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
                        {createMutation.isPending ? createdTask ? "Добавление документов…" : "Создание…" : createdTask ? "Повторить добавление документов" : "Создать задачу"}
                    </Button>
                </>
            }
        >
            <div className="flex flex-col gap-4">
                {documentWarnings.length > 0 && <ErrorMessage title={`Задача ${createdTask?.key ?? ""} создана; не все документы добавлены`} message={`${documentWarnings.join(" ")} Черновики сохранены. Повторная попытка добавит документы в ту же задачу.`} />}
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

                <fieldset disabled={createdTask !== null} className="contents">
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
                    documentRefs={selectedDocumentRefs} files={[...newDocumentFiles.map(draft => draft.file), ...markdownDocuments.map(draft => new File([draft.content_md], `${draft.title}.md`, { type: "text/markdown" }))]}
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

                </fieldset>
                <section className="flex flex-col gap-3" aria-labelledby="task-documents-heading">
                    <p
                        id="task-documents-heading"
                        className="text-[10px] font-semibold tracking-[0.12em] text-muted uppercase"
                    >
                        Документы
                    </p>
                    <TaskDocumentsField
                        projectId={projectId}
                        documents={selectedDocuments}
                        files={newDocumentFiles}
                        markdown={markdownDocuments}
                        disabled={isBusy}
                        onDocumentsChange={setSelectedDocuments}
                        onFilesChange={setNewDocumentFiles}
                        onMarkdownChange={setMarkdownDocuments}
                    />
                </section>
            </div>
        </Modal>
    );
}
