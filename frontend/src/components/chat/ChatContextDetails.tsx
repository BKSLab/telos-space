import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, CheckCircle2, Circle } from "lucide-react";
import { api, ApiError, endpoints, queryKeys } from "@/lib/api";
import { chatContextKey, type ChatContextTarget } from "@/lib/chatContext";
import type { ProjectStage, Task } from "@/lib/types";
import { fullName } from "@/lib/types";
import { useRenderedMarkdown } from "@/lib/useRenderedMarkdown";
import { useUiStore } from "@/stores/ui";
import { PriorityBadge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { DocumentView } from "@/components/documents/DocumentView";
import { DueDate } from "@/components/ui/DueDate";
import { ErrorMessage, Skeleton } from "@/components/ui/States";

interface ChatContextDetailsProps {
    target: ChatContextTarget;
    projectId: number;
    projectKey: string;
    onOpenFull?: () => void;
}

/** Чтение контекста не отмечает задачу просмотренной и не меняет исходный объект. */
export function ChatContextDetails(props: ChatContextDetailsProps) {
    const { target, projectId } = props;
    return (
        <div key={`${projectId}:${chatContextKey(target)}`} className="flex min-w-0 flex-col gap-4">
            {target.type === "task" && <TaskContextDetails {...props} target={target} />}
            {target.type === "document" && <DocumentView documentRef={target.documentRef} fragment={target.fragment} projectKey={props.projectKey} expectedProjectId={projectId} compact />}
        </div>
    );
}

function TaskContextDetails({ target, projectId, onOpenFull }: ChatContextDetailsProps & { target: Extract<ChatContextTarget, { type: "task" }> }) {
    const detail = useQuery({
        queryKey: [...queryKeys.task(target.id), "chat-preview", projectId],
        queryFn: () => api.get<Task>(endpoints.task(target.id)),
        refetchOnMount: "always",
        retry: false,
    });
    const task = detail.data;
    const stages = useQuery({
        queryKey: queryKeys.stages(projectId),
        queryFn: () => api.get<ProjectStage[]>(endpoints.projectStages(projectId)),
        enabled: detail.isSuccess && task?.project_id === projectId,
        retry: false,
    });

    if (detail.error) return <ReadError error={detail.error} busy={detail.isFetching} retry={() => void detail.refetch()} />;
    if (detail.isPending || !task) return <LoadingDetails />;
    if (task.project_id !== projectId) return <WrongProject />;
    if (isAccessError(stages.error)) return <ReadError error={stages.error!} busy={stages.isFetching} retry={() => void stages.refetch()} />;

    const stage = stages.error ? undefined : stages.data?.find((item) => item.id === task.stage_id && item.project_id === projectId);
    const executor = task.participants.find((participant) => participant.role === "EXECUTOR")?.user;
    const executorName = (executor && (fullName(executor) || executor.username)) || task.assignee || "Не назначен";
    const checklist = task.checklist;
    const visibleItems = checklist?.items.slice(0, 6) ?? [];

    return <>
        <header className="flex min-w-0 flex-col gap-1.5">
            <span className="font-mono text-[12px] text-muted">{task.key}</span>
            <h3 className="text-[15px] font-semibold break-words text-primary [overflow-wrap:anywhere]">{task.title}</h3>
        </header>
        <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-2 text-[13px]">
            <dt className="text-muted">Статус</dt>
            <dd className="break-words text-secondary">{stages.isPending && task.stage_id !== null ? "Загрузка…" : stage?.name ?? (task.stage_id === null ? "Без стадии" : "Статус недоступен")}</dd>
            <dt className="text-muted">Исполнитель</dt>
            <dd className="break-words text-secondary">{executorName}</dd>
            <dt className="text-muted">Срок</dt>
            <dd>{task.due_date ? <DueDate value={task.due_date} isDone={stage?.is_done_stage ?? Boolean(task.completed_at)} className="text-[13px]" /> : <span className="text-secondary">Не указан</span>}</dd>
            <dt className="text-muted">Приоритет</dt>
            <dd><PriorityBadge priority={task.priority} showLow /></dd>
        </dl>
        {stages.error && <ReadError error={stages.error} busy={stages.isFetching} retry={() => void stages.refetch()} />}
        <section aria-label="Описание задачи" className="flex min-w-0 flex-col gap-2">
            <h4 className="text-[13px] font-semibold text-secondary">Описание</h4>
            {task.description_md?.trim() ? <Markdown markdown={task.description_md} /> : <p className="text-[13px] text-muted">Описания пока нет.</p>}
        </section>
        {checklist && <section aria-label="Чек-лист задачи" className="flex min-w-0 flex-col gap-2">
            <div className="flex flex-wrap items-baseline gap-2">
                <h4 className="text-[13px] font-semibold break-words text-secondary">{checklist.title}</h4>
                <span className="text-[12px] text-muted">Выполнено {checklist.items.filter((item) => item.is_completed).length} из {checklist.items.length}</span>
            </div>
            <ul className="flex min-w-0 flex-col gap-2">
                {visibleItems.map((item) => <li key={item.id} className="flex min-w-0 items-start gap-2 text-[13px] break-words text-secondary [overflow-wrap:anywhere]">
                    {item.is_completed ? <CheckCircle2 size={15} className="mt-0.5 shrink-0 text-muted" aria-hidden="true" /> : <Circle size={15} className="mt-0.5 shrink-0 text-muted" aria-hidden="true" />}
                    <span><span className="sr-only">{item.is_completed ? "Выполнено: " : "Не выполнено: "}</span>{item.text}</span>
                </li>)}
            </ul>
            {checklist.items.length > visibleItems.length && <p className="text-[12px] text-muted">Ещё пунктов: {checklist.items.length - visibleItems.length}. Полный чек-лист — в карточке задачи.</p>}
        </section>}
        <AnswerExcerpt excerpt={target.excerpt} />
        <Button className="self-start" icon={<ArrowUpRight size={14} />} onClick={() => {
            onOpenFull?.();
            useUiStore.getState().setSelectedTaskId(task.id);
        }}>Открыть карточку задачи</Button>
    </>;
}

function Markdown({ markdown }: { markdown: string }) {
    const html = useRenderedMarkdown(markdown);
    // Общий renderer очищает HTML через DOMPurify.
    return <div className="markdown-body min-w-0 text-[13px] break-words [overflow-wrap:anywhere]" dangerouslySetInnerHTML={{ __html: html }} />;
}

function AnswerExcerpt({ excerpt }: { excerpt?: string | null }) {
    if (!excerpt?.trim()) return null;
    return <section aria-label="Фрагмент из ответа" className="flex min-w-0 flex-col gap-2 border-l-2 border-line pl-3">
        <h4 className="text-[12px] font-semibold text-muted">Фрагмент из ответа</h4>
        <p className="whitespace-pre-wrap text-[13px] leading-relaxed break-words text-secondary [overflow-wrap:anywhere]">{excerpt}</p>
    </section>;
}

function LoadingDetails() {
    return <div role="status" aria-label="Загрузка контекста" className="flex flex-col gap-3"><Skeleton className="h-8 w-3/4" /><Skeleton className="h-36 w-full" /></div>;
}

function WrongProject() {
    return <ErrorMessage title="Объект недоступен" message="Этот объект недоступен в текущем проекте." />;
}

function isAccessError(error: Error | null): boolean {
    return error instanceof ApiError && (error.status === 403 || error.status === 404);
}

function ReadError({ error, busy, retry }: { error: Error; busy: boolean; retry: () => void }) {
    return <ErrorMessage title="Не удалось открыть объект" message={isAccessError(error) ? "Объект удалён или у вас нет доступа к нему." : error.message} action={
        <Button size="sm" disabled={busy} onClick={retry}>Повторить загрузку</Button>
    } />;
}
