import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlignLeft, FileText, FolderInput, History, Link2, ListChecks, MessageSquare, Paperclip, Pencil, Trash2, Users } from "lucide-react";
import { Tab, TabList, TabPanel, Tabs } from "react-aria-components";
import { api, ApiError, endpoints, queryKeys } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, toDateInputValue } from "@/lib/dates";
import type {
    DocumentListItem,
    LinkedDocument,
    ProjectMember,
    ProjectStage,
    Task,
    TaskActivity,
    TaskAttachment,
    TaskComment,
    TaskPriority,
    TaskStatus,
    TaskUpdate,
    WbsStructure,
} from "@/lib/types";
import { PRIORITY_LABELS, PRIORITY_ORDER } from "@/lib/types";
import { useUiStore } from "@/stores/ui";
import { useRenderedMarkdown } from "@/lib/useRenderedMarkdown";
import { Button, IconButton } from "@/components/ui/Button";
import { DrawerSection } from "@/components/ui/Drawer";
import { Modal } from "@/components/ui/Modal";
import { Field, Input, Select, Textarea } from "@/components/ui/Field";
import { PriorityBadge, StatusDot } from "@/components/ui/Badge";
import { DueDate } from "@/components/ui/DueDate";
import { ErrorMessage, Skeleton } from "@/components/ui/States";
import { TaskFilesSection } from "@/components/tasks/TaskFilesSection";
import { TaskCommentsSection } from "@/components/tasks/TaskCommentsSection";
import { observerUserIds, participantUserId } from "@/lib/taskParticipants";
import { useCurrentUser } from "@/lib/useAuth";
import { TaskPeopleFields } from "@/components/tasks/TaskPeopleFields";
import { TaskRiskSection } from "@/components/risks/TaskRiskSection";
import { TaskChecklistSection } from "@/components/tasks/TaskChecklistSection";
import { TaskReportSection } from "@/components/tasks/TaskReportSection";
import { TaskTitleEditor } from "@/components/tasks/TaskTitleEditor";
import { MoveTaskDialog } from "@/components/tasks/MoveTaskDialog";
import { LocalUserSelect } from "@/components/tasks/LocalUserSelect";
import { isClosedStatus, TASK_STATUS_LABELS, TASK_STATUS_ORDER, taskKind } from "@/lib/tasks";
import { moveDeniedHint } from "@/lib/taskMove";
import { useTaskViewed } from "@/lib/useTaskViewed";
import type { TaskViewToken } from "@/lib/taskAttention";
import { taskFilesPollInterval } from "@/lib/projectFiles";
import { useMediaQuery } from "@/lib/useCalendarView";

const SECTIONS = [
    { id: "overview", label: "Основное", icon: AlignLeft },
    { id: "people", label: "Участники", icon: Users },
    { id: "checklist", label: "Чек-лист", icon: ListChecks },
    { id: "links", label: "Связи", icon: Link2 },
    { id: "files", label: "Файлы", icon: Paperclip },
    { id: "comments", label: "Комментарии", icon: MessageSquare },
    { id: "report", label: "Отчёт", icon: FileText },
    { id: "history", label: "История", icon: History },
] as const;
type TaskSection = typeof SECTIONS[number]["id"];
type TaskDateField = "start_date" | "due_date";
type TaskDateDraft = { value: string; revision: number | undefined; changed: boolean };
const PANEL_CLASS = "scrollbar-thin min-h-0 min-w-0 flex-1 overflow-y-auto outline-none data-[inert]:hidden data-[focus-visible]:outline-2 data-[focus-visible]:-outline-offset-2 data-[focus-visible]:outline-accent/70";
const TASK_CONFLICT_MESSAGE = "Задача уже изменена. Правки остались в черновике. Сверьте актуальные данные перед повторным сохранением.";

const EVENT_LABELS: Record<TaskActivity["event_type"], string> = {
    STAGE_CHANGED: "Стадия",
    DUE_DATE_CHANGED: "Срок",
    START_DATE_CHANGED: "Начало",
    BASELINE_CHANGED: "Baseline",
    DESCRIPTION_CHANGED: "Описание изменено",
    PRIORITY_CHANGED: "Приоритет",
    ASSIGNEE_CHANGED: "Исполнитель",
    WBS_NODE_CHANGED: "Раздел ИСР",
    COMMENT_ADDED: "Добавлен комментарий",
    CHECKLIST_CHANGED: "Чек-лист изменён",
    TASK_MOVED: "Перенесена",
    LINKS_REMOVED: "При переносе снято",
};

function MarkdownBlock({ markdown }: { markdown: string }) {
    const html = useRenderedMarkdown(markdown);
    return (
        <div
            className="markdown-body text-[13px]"
            // Содержимое проходит через DOMPurify в renderMarkdown.
            dangerouslySetInnerHTML={{ __html: html }}
        />
    );
}

/**
 * Единая карточка задачи для канбана, списка и структуры: одна сущность
 * не должна иметь разных представлений на разных экранах (раздел 12).
 */
export function TaskDialog() {
    const taskId = useUiStore((state) => state.selectedTaskId);
    const setSelectedTaskId = useUiStore((state) => state.setSelectedTaskId);

    if (taskId === null) {
        return null;
    }
    // key сбрасывает черновики и локальные ошибки при переходе к другой задаче.
    return (
        <TaskDialogContent
            key={taskId}
            taskId={taskId}
            onClose={() => setSelectedTaskId(null)}
        />
    );
}

function TaskDialogContent({ taskId, onClose }: { taskId: number; onClose: () => void }) {
    const queryClient = useQueryClient();
    const vertical = useMediaQuery("(min-width: 640px)");
    const [section, setSection] = useState<TaskSection>("overview");
    const [isEditingDescription, setIsEditingDescription] = useState(false);
    const [descriptionDraft, setDescriptionDraft] = useState("");
    const [descriptionRevision, setDescriptionRevision] = useState<number | undefined>();
    const [dateDrafts, setDateDrafts] = useState<Partial<Record<TaskDateField, TaskDateDraft>>>({});
    const visibleCardRef = useRef<HTMLDivElement>(null);
    const reportTabRef = useRef<HTMLDivElement>(null);
    const [reportComment, setReportComment] = useState<TaskComment | null>(null);
    const [moveDialogOpen, setMoveDialogOpen] = useState(false);
    const [documentToLink, setDocumentToLink] = useState("");
    const currentUserQuery = useCurrentUser();

    const taskQuery = useQuery({
        queryKey: queryKeys.task(taskId),
        queryFn: async () => {
            const attention_view = await api.get<TaskViewToken>(`/api/v1/tasks/${taskId}/attention`);
            return { ...await api.get<Task>(endpoints.task(taskId)), attention_view };
        },
    });
    const task = taskQuery.data;
    const projectId = task?.project_id ?? undefined;
    const kind = task ? taskKind(task) : "PROJECT";
    const personal = kind === "PERSONAL";
    const sections = SECTIONS.filter(item => item.id !== "links" || !personal);
    const selectedSection = sections.some(item => item.id === section) ? section : "overview";
    useEffect(() => {
        const frame = requestAnimationFrame(() => {
            visibleCardRef.current?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')
                ?.scrollIntoView({ block: "nearest", inline: "nearest" });
        });
        return () => cancelAnimationFrame(frame);
    }, [selectedSection, vertical, task?.id]);

    const stagesQuery = useQuery({
        queryKey: queryKeys.stages(projectId ?? 0),
        queryFn: () => api.get<ProjectStage[]>(endpoints.projectStages(projectId as number)),
        enabled: projectId !== undefined,
    });
    const membersQuery = useQuery({
        queryKey: queryKeys.projectMembers(projectId ?? 0),
        queryFn: () => api.get<ProjectMember[]>(endpoints.projectMembers(projectId as number)),
        enabled: projectId !== undefined,
    });
    const commentsQuery = useQuery({
        queryKey: queryKeys.taskComments(taskId),
        queryFn: () => api.get<TaskComment[]>(endpoints.taskComments(taskId)),
    });
    const activityQuery = useQuery({
        queryKey: queryKeys.taskActivity(taskId),
        queryFn: () => api.get<TaskActivity[]>(endpoints.taskActivity(taskId)),
    });
    const attachmentsQuery = useQuery({
        queryKey: queryKeys.taskAttachments(taskId),
        queryFn: () => api.get<TaskAttachment[]>(endpoints.taskAttachments(taskId)),
        refetchInterval: query => taskFilesPollInterval(query.state.data, query.state.status === "error"),
    });
    const linksQuery = useQuery({
        queryKey: queryKeys.taskLinks(taskId),
        queryFn: () => api.get<LinkedDocument[]>(endpoints.taskLinks(taskId)),
        enabled: projectId !== undefined,
    });
    const documentsQuery = useQuery({
        queryKey: queryKeys.documents(projectId ?? 0),
        queryFn: () => api.get<DocumentListItem[]>(endpoints.projectDocuments(projectId as number)),
        enabled: projectId !== undefined,
    });
    const wbsQuery = useQuery({
        queryKey: queryKeys.wbs(projectId ?? 0),
        queryFn: () => api.get<WbsStructure>(endpoints.wbs(projectId as number)),
        enabled: projectId !== undefined && task?.wbs_node_id !== null,
    });

    useTaskViewed(taskId, commentsQuery.isSuccess && !commentsQuery.isFetching && attachmentsQuery.isSuccess && !attachmentsQuery.isFetching && activityQuery.isSuccess && !activityQuery.isFetching ? task?.attention_view : undefined, visibleCardRef);

    /** После любого изменения задачи обновляем все её представления. */
    const invalidateTask = () => {
        queryClient.invalidateQueries({ queryKey: queryKeys.task(taskId) });
        queryClient.invalidateQueries({ queryKey: queryKeys.taskActivity(taskId) });
        queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
        queryClient.invalidateQueries({ queryKey: queryKeys.myTasks });
        if (projectId !== undefined) {
            queryClient.invalidateQueries({ queryKey: ["projects", projectId] });
        }
    };

    const updateMutation = useMutation({
        mutationFn: (data: TaskUpdate) =>
            // Черновики передают свою версию явно, включая undefined для старого API.
            api.patch<Task>(endpoints.task(taskId), { expected_revision: task?.revision, ...data }),
        retry: false,
        onSuccess: (updated, data) => {
            queryClient.setQueryData(queryKeys.task(taskId), updated);
            if ("description_md" in data) setIsEditingDescription(false);
            for (const field of ["start_date", "due_date"] as const) {
                if (field in data) clearDateDraft(field);
            }
            invalidateTask();
        },
        onError: error => {
            if (error instanceof ApiError && error.status === 409) invalidateTask();
        },
    });

    function clearDateDraft(field: TaskDateField) {
        setDateDrafts(current => {
            const next = { ...current };
            delete next[field];
            return next;
        });
    }

    function beginDateEdit(field: TaskDateField) {
        setDateDrafts(current => current[field] ? current : {
            ...current,
            [field]: { value: toDateInputValue(task?.[field] ?? null), revision: task?.revision, changed: false },
        });
    }

    function changeDate(field: TaskDateField, value: string) {
        const draft = dateDrafts[field];
        const revision = draft ? draft.revision : task?.revision;
        setDateDrafts(current => ({ ...current, [field]: { value, revision, changed: true } }));
        updateMutation.mutate({ [field]: value || null, expected_revision: revision });
    }

    const updateErrorMessage = updateMutation.error instanceof ApiError && updateMutation.error.status === 409
        ? TASK_CONFLICT_MESSAGE : updateMutation.error?.message;

    const moveMutation = useMutation({
        mutationFn: (stageId: number) =>
            api.patch<Task>(endpoints.taskMove(taskId), { stage_id: stageId }),
        onSuccess: invalidateTask,
    });

    const statusMutation = useMutation({
        mutationFn: (status: TaskStatus) => api.patch<Task>(endpoints.taskStatus(taskId), { status }),
        onSuccess: invalidateTask,
    });

    const linkMutation = useMutation({
        mutationFn: (documentId: number) =>
            api.post(endpoints.links(), { document_id: documentId, task_id: taskId }),
        onSuccess: () => {
            setDocumentToLink("");
            queryClient.invalidateQueries({ queryKey: queryKeys.taskLinks(taskId) });
            invalidateTask();
        },
    });

    const unlinkMutation = useMutation({
        mutationFn: (linkId: number) => api.delete(endpoints.link(linkId)),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: queryKeys.taskLinks(taskId) });
            invalidateTask();
        },
    });

    const unlinkedDocuments = useMemo(() => {
        const linkedIds = new Set((linksQuery.data ?? []).map((link) => link.document_id));
        return (documentsQuery.data ?? []).filter((document) => !linkedIds.has(document.id));
    }, [documentsQuery.data, linksQuery.data]);

    const stage = stagesQuery.data?.find((item) => item.id === task?.stage_id);
    const wbsNode = wbsQuery.data?.nodes.find((node) => node.id === task?.wbs_node_id);
    const sectionCounts: Partial<Record<TaskSection, number | string>> = {
        files: attachmentsQuery.data?.length,
        comments: commentsQuery.data?.length,
        history: activityQuery.data?.length,
        checklist: task?.checklist ? `${task.checklist.items.filter(item => item.is_completed).length}/${task.checklist.items.length}` : undefined,
    };
    const canChangeReporter = personal ? participantUserId(task!, "REPORTER") === currentUserQuery.data?.id : membersQuery.data?.some(
        (member) =>
            member.user.id === currentUserQuery.data?.id && member.role === "OWNER",
    );
    const mutationError =
        statusMutation.error ??
        updateMutation.error ??
        moveMutation.error ??
        linkMutation.error ??
        unlinkMutation.error;

    const header = task ? (
        <div className="flex min-w-0 flex-col gap-1.5">
            <div className="flex items-center gap-2">
                <span className="font-mono text-[11px] text-muted">{task.key}</span>
                <PriorityBadge priority={task.priority} showLow />
            </div>
            <TaskTitleEditor title={task.title} revision={task.revision} canEdit saving={updateMutation.isPending} error={updateErrorMessage}
                onEditStart={updateMutation.reset}
                onSave={async (title, revision) => { try { await updateMutation.mutateAsync({ title, expected_revision: revision }); return true; } catch { return false; } }} />
            <Button size="sm" variant="ghost" className="self-start" icon={<FolderInput size={13} />} disabled={Boolean(moveDeniedHint(kind, true, participantUserId(task, "REPORTER") === currentUserQuery.data?.id))}
                title={moveDeniedHint(kind, true, participantUserId(task, "REPORTER") === currentUserQuery.data?.id)} onClick={() => setMoveDialogOpen(true)}>Переместить…</Button>
        </div>
    ) : (
        <Skeleton className="h-9 w-56" />
    );

    return (
        <Modal
            title={task ? `Задача ${task.key}` : "Задача"}
            isOpen
            onOpenChange={open => { if (!open) onClose(); }}
            header={header}
            size="xl"
            layout="workspace"
            closeLabel="Закрыть задачу"
        >
            {taskQuery.isPending && (
                <div className="flex flex-col gap-3 p-4">
                    <Skeleton className="h-20 w-full" />
                    <Skeleton className="h-32 w-full" />
                </div>
            )}
            {taskQuery.error && (
                <div className="p-4">
                    <ErrorMessage message={(taskQuery.error as Error).message} />
                </div>
            )}

            {task && (
                <div ref={visibleCardRef} className="flex min-h-0 flex-1 flex-col">
                    {mutationError && (
                        <div className="shrink-0 border-b border-line px-5 py-3">
                            <ErrorMessage title="Не удалось сохранить изменения" message={mutationError === updateMutation.error ? updateErrorMessage! : (mutationError as Error).message} />
                        </div>
                    )}

                    <Tabs selectedKey={selectedSection} onSelectionChange={key => setSection(key as TaskSection)} orientation={vertical ? "vertical" : "horizontal"} className="flex min-h-0 flex-1 flex-col sm:flex-row">
                        <TabList aria-label="Разделы задачи" className="scrollbar-thin flex shrink-0 gap-1 overflow-x-auto border-b border-line bg-app/25 p-2 sm:w-48 sm:flex-col sm:overflow-x-hidden sm:overflow-y-auto sm:border-r sm:border-b-0 sm:p-3">
                            {sections.map(({ id, label, icon: Icon }) => <Tab key={id} id={id} ref={id === "report" ? reportTabRef : undefined} aria-label={label} className="brand-tab flex min-h-10 shrink-0 cursor-pointer items-center gap-2.5 rounded-[var(--radius-control)] border border-transparent px-3 py-2 text-[13px] whitespace-nowrap text-secondary outline-none transition-colors hover:bg-hover data-[selected]:border-line data-[selected]:bg-accent/[0.075] data-[selected]:text-primary data-[focus-visible]:ring-2 data-[focus-visible]:ring-accent">
                                <Icon size={16} aria-hidden="true" className="shrink-0 text-muted" />
                                <span>{label}</span>
                                {sectionCounts[id] !== undefined && <span className="ml-auto rounded bg-control px-1.5 py-0.5 font-mono text-[10px] text-muted">{sectionCounts[id]}</span>}
                            </Tab>)}
                        </TabList>
                        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
                            <TabPanel id="overview" shouldForceMount className={PANEL_CLASS}>
                                <DrawerSection title="Свойства">
                                    <div className="grid gap-3 sm:grid-cols-2">
                                        {personal ? <Field label="Статус">{id => <Select id={id} value={task.status ?? "NEW"} disabled={statusMutation.isPending} onChange={event => statusMutation.mutate(event.target.value as TaskStatus)}>{TASK_STATUS_ORDER.map(status => <option key={status} value={status}>{TASK_STATUS_LABELS[status]}</option>)}</Select>}</Field> : <Field label="Стадия">
                                            {(id) => (
                                                <Select
                                                    id={id}
                                                    value={task.stage_id ?? ""}
                                                    disabled={stagesQuery.isPending || moveMutation.isPending}
                                                    onChange={(event) =>
                                                        moveMutation.mutate(Number(event.target.value))
                                                    }
                                                >
                                                    {stagesQuery.data?.map((item) => (
                                                        <option key={item.id} value={item.id}>
                                                            {item.name}
                                                        </option>
                                                    ))}
                                                </Select>
                                            )}
                                        </Field>}

                                        <Field label="Приоритет">
                                            {(id) => (
                                                <Select
                                                    id={id}
                                                    value={task.priority}
                                                    disabled={updateMutation.isPending}
                                                    onChange={(event) =>
                                                        updateMutation.mutate({
                                                            priority: event.target.value as TaskPriority,
                                                        })
                                                    }
                                                >
                                                    {PRIORITY_ORDER.map((priority) => (
                                                        <option key={priority} value={priority}>
                                                            {PRIORITY_LABELS[priority]}
                                                        </option>
                                                    ))}
                                                </Select>
                                            )}
                                        </Field>

                                        <Field label="Начало">
                                            {(id) => (
                                                <Input
                                                    id={id}
                                                    type="date"
                                                    value={dateDrafts.start_date?.value ?? toDateInputValue(task.start_date)}
                                                    max={task.due_date ?? undefined}
                                                    disabled={updateMutation.isPending}
                                                    onFocus={() => beginDateEdit("start_date")}
                                                    onBlur={() => { if (!dateDrafts.start_date?.changed) clearDateDraft("start_date"); }}
                                                    onChange={event => changeDate("start_date", event.target.value)}
                                                />
                                            )}
                                        </Field>

                                        <Field label="Завершение">
                                            {(id) => (
                                                <Input
                                                    id={id}
                                                    type="date"
                                                    value={dateDrafts.due_date?.value ?? toDateInputValue(task.due_date)}
                                                    min={task.start_date ?? undefined}
                                                    disabled={updateMutation.isPending}
                                                    onFocus={() => beginDateEdit("due_date")}
                                                    onBlur={() => { if (!dateDrafts.due_date?.changed) clearDateDraft("due_date"); }}
                                                    onChange={event => changeDate("due_date", event.target.value)}
                                                />
                                            )}
                                        </Field>

                                    </div>

                                    <div className="mt-3 flex items-center gap-3 text-[11px] text-muted">
                                        {stage && (
                                            <span className="inline-flex items-center gap-1.5">
                                                <StatusDot color={stage.color} />
                                                {stage.name}
                                            </span>
                                        )}
                                        <DueDate value={task.due_date} isDone={personal ? isClosedStatus(task.status) : stage?.is_done_stage} />
                                    </div>
                                </DrawerSection>

                                <DrawerSection
                                    title="Описание"
                                    action={
                                        !isEditingDescription && (
                                            <IconButton
                                                label="Редактировать описание"
                                                size="sm"
                                                disabled={updateMutation.isPending}
                                                onClick={() => {
                                                    updateMutation.reset();
                                                    setDescriptionDraft(task.description_md ?? "");
                                                    setDescriptionRevision(task.revision);
                                                    setIsEditingDescription(true);
                                                }}
                                            >
                                                <Pencil size={13} aria-hidden="true" />
                                            </IconButton>
                                        )
                                    }
                                >
                                    {isEditingDescription ? (
                                        <div className="flex flex-col gap-2">
                                            <Textarea
                                                rows={8}
                                                value={descriptionDraft}
                                                aria-label="Описание задачи в Markdown"
                                                disabled={updateMutation.isPending}
                                                onChange={(event) => setDescriptionDraft(event.target.value)}
                                            />
                                            <div className="flex justify-end gap-2">
                                                <Button
                                                    size="sm"
                                                    disabled={updateMutation.isPending}
                                                    onClick={() => setIsEditingDescription(false)}
                                                >
                                                    Отмена
                                                </Button>
                                                <Button
                                                    size="sm"
                                                    variant="primary"
                                                    disabled={updateMutation.isPending}
                                                    onClick={() =>
                                                        updateMutation.mutate({
                                                            description_md: descriptionDraft || null,
                                                            expected_revision: descriptionRevision,
                                                        })
                                                    }
                                                >
                                                    Сохранить
                                                </Button>
                                            </div>
                                        </div>
                                    ) : task.description_md ? (
                                        <MarkdownBlock markdown={task.description_md} />
                                    ) : (
                                        <p className="text-[13px] text-muted">
                                            Описания пока нет. Добавьте контекст, чтобы задача была понятна.
                                        </p>
                                    )}
                                </DrawerSection>
                            </TabPanel>
                            <TabPanel id="people" shouldForceMount className={PANEL_CLASS}>
                                <DrawerSection title="Участники">
                                    <div className="min-w-0">
                                        {personal ? <div className="grid gap-3 sm:grid-cols-2">
                                            <Field label="Исполнитель">{() => <LocalUserSelect label="Исполнитель" selected={(task.participants ?? []).filter(item => item.role === "EXECUTOR").map(item => item.user)} disabled={updateMutation.isPending} onChange={users => updateMutation.mutate({ executor_id: users[0]?.id ?? null })} />}</Field>
                                            <Field label="Постановщик">{() => <LocalUserSelect label="Постановщик" selected={(task.participants ?? []).filter(item => item.role === "REPORTER").map(item => item.user)} disabled={!canChangeReporter || updateMutation.isPending} onChange={users => updateMutation.mutate({ reporter_id: users[0]?.id ?? null })} />}</Field>
                                            <div className="sm:col-span-2"><Field label="Наблюдатели">{() => <LocalUserSelect label="Наблюдатели" multiple selected={(task.participants ?? []).filter(item => item.role === "OBSERVER").map(item => item.user)} disabled={updateMutation.isPending} onChange={users => updateMutation.mutate({ observer_ids: users.map(user => user.id) })} />}</Field></div>
                                        </div> : <TaskPeopleFields
                                            members={membersQuery.data ?? []}
                                            executorId={participantUserId(task, "EXECUTOR")}
                                            reporterId={participantUserId(task, "REPORTER")}
                                            observerIds={observerUserIds(task)}
                                            canChangeReporter={canChangeReporter}
                                            legacyExecutorLabel={task.assignee}
                                            disabled={
                                                membersQuery.isPending || updateMutation.isPending
                                            }
                                            onExecutorChange={(userId) =>
                                                updateMutation.mutate({ executor_id: userId })
                                            }
                                            onReporterChange={(userId) =>
                                                updateMutation.mutate({ reporter_id: userId })
                                            }
                                            onObserversChange={(userIds) =>
                                                updateMutation.mutate({ observer_ids: userIds })
                                            }
                                        />
                                        }
                                        {membersQuery.error && (
                                            <p className="mt-2 text-[11px] text-danger">
                                                Не удалось загрузить команду проекта.
                                            </p>
                                        )}
                                    </div>
                                </DrawerSection>
                            </TabPanel>
                            <TabPanel id="checklist" shouldForceMount className={PANEL_CLASS}>
                                <div className="px-5 py-5">
                                    <TaskChecklistSection task={task} description={isEditingDescription ? descriptionDraft : task.description_md ?? ""} />
                                </div>
                            </TabPanel>
                            {projectId !== undefined && <>
                            <TabPanel id="links" shouldForceMount className={PANEL_CLASS}>
                                <DrawerSection title="Раздел ИСР"><p className="text-[13px] text-secondary">{wbsNode ? wbsNode.title : "Не распределена"}</p></DrawerSection>
                                {projectId !== undefined && <TaskRiskSection projectId={projectId} projectKey={task.key.slice(0, task.key.lastIndexOf("-"))} taskId={task.id} />}
                                {projectId !== undefined && <DrawerSection title="Документы" count={linksQuery.data?.length ?? null}>
                                    <div className="flex flex-col gap-2">
                                        {linksQuery.data?.map((link) => (
                                            <div
                                                key={link.link_id}
                                                className="flex items-center gap-2 rounded-[var(--radius-control)] bg-white/[0.03] px-2.5 py-1.5"
                                            >
                                                <Link2 size={13} className="shrink-0 text-muted" aria-hidden="true" />
                                                <span className="min-w-0 flex-1 truncate text-[13px] text-secondary">
                                                    {link.title}
                                                </span>
                                                <IconButton
                                                    label={`Отвязать документ ${link.title}`}
                                                    size="sm"
                                                    disabled={unlinkMutation.isPending}
                                                    onClick={() => unlinkMutation.mutate(link.link_id)}
                                                >
                                                    <Trash2 size={12} aria-hidden="true" />
                                                </IconButton>
                                            </div>
                                        ))}
                                        {unlinkedDocuments.length > 0 && (
                                            <div className="flex gap-2">
                                                <Select
                                                    aria-label="Документ для связи с задачей"
                                                    value={documentToLink}
                                                    onChange={(event) => setDocumentToLink(event.target.value)}
                                                >
                                                    <option value="">Связать документ…</option>
                                                    {unlinkedDocuments.map((document) => (
                                                        <option key={document.id} value={document.id}>
                                                            {document.title}
                                                        </option>
                                                    ))}
                                                </Select>
                                                <Button
                                                    size="sm"
                                                    disabled={documentToLink === "" || linkMutation.isPending}
                                                    onClick={() => linkMutation.mutate(Number(documentToLink))}
                                                >
                                                    Связать
                                                </Button>
                                            </div>
                                        )}
                                        {linksQuery.data?.length === 0 && unlinkedDocuments.length === 0 && (
                                            <p className="text-[13px] text-muted">
                                                В проекте пока нет документов для связи.
                                            </p>
                                        )}
                                    </div>
                                </DrawerSection>}
                            </TabPanel>
                            </>}
                            <TabPanel id="files" shouldForceMount className={PANEL_CLASS}>
                                <DrawerSection title="Файлы" count={attachmentsQuery.data?.length ?? null}>
                                    <TaskFilesSection taskId={taskId} attachments={attachmentsQuery.data ?? []} linkedDocuments={linksQuery.data ?? []}
                                        projectKey={projectId !== undefined ? task.key.slice(0, task.key.lastIndexOf("-")) : undefined}
                                        onChange={invalidateTask} loading={attachmentsQuery.isPending} error={attachmentsQuery.error?.message} />
                                </DrawerSection>
                            </TabPanel>
                            <TabPanel id="comments" shouldForceMount className={PANEL_CLASS}>
                                <DrawerSection title="Комментарии" count={commentsQuery.data?.length ?? null}>
                                    <TaskCommentsSection taskId={taskId} comments={commentsQuery.data ?? []} attachments={attachmentsQuery.data ?? []}
                                        currentUserId={currentUserQuery.data?.id} onChange={invalidateTask} loading={commentsQuery.isPending} error={commentsQuery.error?.message}
                                        onReport={comment => { setReportComment(comment); setSection("report"); requestAnimationFrame(() => reportTabRef.current?.focus()); }} />
                                </DrawerSection>
                            </TabPanel>
                            <TabPanel id="report" shouldForceMount className={PANEL_CLASS}>
                                <DrawerSection title="Итоговый отчёт"><TaskReportSection taskId={taskId} active={selectedSection === "report"} selectedComment={reportComment} clearComment={() => setReportComment(null)} /></DrawerSection>
                            </TabPanel>
                            <TabPanel id="history" shouldForceMount className={PANEL_CLASS}>
                                <DrawerSection title="История" count={activityQuery.data?.length ?? null}>
                                    <ol className="flex flex-col gap-2">
                                        {activityQuery.data?.map((event) => (
                                            <li key={event.id} className="flex gap-2.5 text-[12px]">
                                                <span
                                                    aria-hidden="true"
                                                    className="mt-1.5 size-1.5 shrink-0 rounded-full bg-line-strong"
                                                />
                                                <div className="min-w-0 flex-1">
                                                    <p className="text-secondary">
                                                        <span className="text-muted">
                                                            {EVENT_LABELS[event.event_type]}
                                                        </span>
                                                        {event.from_value && event.to_value && (
                                                            <span className="ml-1">
                                                                <span className="text-disabled line-through">
                                                                    {event.from_value}
                                                                </span>
                                                                <span className="mx-1 text-disabled">→</span>
                                                                <span>{event.to_value}</span>
                                                            </span>
                                                        )}
                                                        {!event.from_value && event.to_value && (
                                                            <span className="ml-1">{event.to_value}</span>
                                                        )}
                                                    </p>
                                                    {event.actor_name && <p className="text-[11px] text-muted">{event.actor_name}{event.via_api_key_label ? ` · через ${event.via_api_key_label}` : ""}</p>}
                                                    <time
                                                        dateTime={event.created_at}
                                                        className={cn("text-[11px] text-disabled")}
                                                    >
                                                        {formatDateTime(event.created_at)}
                                                    </time>
                                                </div>
                                            </li>
                                        ))}
                                        {activityQuery.data?.length === 0 && (
                                            <li className="text-[13px] text-muted">Изменений пока не было.</li>
                                        )}
                                    </ol>
                                </DrawerSection>
                            </TabPanel>
                        </div>
                    </Tabs>
                    {moveDialogOpen && <MoveTaskDialog task={task} kind={kind} onClose={() => setMoveDialogOpen(false)} onMoved={() => { invalidateTask(); if (projectId) queryClient.invalidateQueries({ queryKey: queryKeys.wbs(projectId) }); }} />}
                </div>
            )}
        </Modal>
    );
}
