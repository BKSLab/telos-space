import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { TaskComment } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Field";
import { UserIdentity } from "@/components/users/UserIdentity";
import { ErrorMessage, Skeleton } from "@/components/ui/States";
import { formatDateTime } from "@/lib/dates";
import { useRenderedMarkdown } from "@/lib/useRenderedMarkdown";

interface Report {
    id: number | null;
    task_id: number;
    revision: number;
    body_md: string;
    author_name: string | null;
    author_id?: number | null;
    created_at: string | null;
    source_comment_id: number | null;
    source_comment_original_id: number | null;
    source_author_name: string | null;
}

export function TaskReportSection({ taskId, active, selectedComment, clearComment, onPublishingChange, canEdit = true, readOnlyHint, targetRevision, targetReportId }: {
    targetRevision?: number; targetReportId?: number;
    taskId: number; active: boolean; selectedComment: TaskComment | null; clearComment: () => void; onPublishingChange?: (busy: boolean) => void; canEdit?: boolean; readOnlyHint?: string;
}) {
    const client = useQueryClient();
    const path = `/api/v1/tasks/${taskId}/report`;
    const key = ["tasks", taskId, "report"];
    const report = useQuery({ queryKey: key, queryFn: () => api.get<Report>(path), enabled: active, retry: false });
    const [historyOpen, setHistoryOpen] = useState(false);
    const targeted = targetRevision !== undefined || targetReportId !== undefined;
    const history = useQuery({ queryKey: [...key, "history"], queryFn: () => api.get<Report[]>(path + "/history"), enabled: active && historyOpen });
    const targetQuery = useQuery({ queryKey: [...key, "history", "target", targetReportId, targetRevision],
        queryFn: () => api.get<Report[]>(path + "/history" + (targetReportId !== undefined ? `?report_id=${targetReportId}` : `?revision=${targetRevision}`)), enabled: active && targeted });
    const selectedReport = targetQuery.data?.find(item => targetReportId !== undefined ? item.id === targetReportId : item.revision === targetRevision);
    const targetRef = useRef<HTMLElement>(null);
    useEffect(() => {
        if (active && selectedReport) {
            targetRef.current?.scrollIntoView?.({ block: "start" });
            targetRef.current?.focus({ preventScroll: true });
        }
    }, [active, selectedReport]);
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState("");
    const [baseRevision, setBaseRevision] = useState(0);
    const reportHtml = useRenderedMarkdown(report.data?.body_md ?? "");
    const publish = useMutation({
        mutationFn: async (comment: TaskComment | null) => {
            onPublishingChange?.(true);
            try {
                const expectedRevision = comment ? report.data!.revision : baseRevision;
                return await (comment
                    ? api.post<Report>(path + "/from-comment", { comment_id: comment.id, expected_revision: expectedRevision, expected_comment_body_md: comment.body_md })
                    : api.post<Report>(path, { body_md: draft, expected_revision: expectedRevision }));
            } finally {
                onPublishingChange?.(false);
            }
        },
        onSuccess: (saved) => {
            client.setQueryData(key, saved);
            setDraft("");
            setEditing(false);
            clearComment();
            void client.invalidateQueries({ queryKey: [...key, "history"] });
        },
    });
    const beginEditing = () => {
        setDraft(report.data?.body_md ?? "");
        setBaseRevision(report.data?.revision ?? 0);
        setEditing(true);
        publish.reset();
    };
    const cancelEditing = () => {
        setEditing(false);
        setDraft("");
        clearComment();
        publish.reset();
    };
    if (report.isPending) return <div role="status" aria-label="Загрузка отчёта" className="flex flex-col gap-3"><Skeleton className="h-4 w-full" /><Skeleton className="h-4 w-5/6" /><Skeleton className="h-4 w-2/3" /><Skeleton className="h-8 w-36" /></div>;
    if (report.error) return <div><ErrorMessage title="Не удалось загрузить отчёт" message={(report.error as Error).message} /><Button variant="ghost" onClick={() => void report.refetch()}>Повторить</Button></div>;
    const hasReport = Boolean(report.data?.body_md);
    return <div className="flex flex-col gap-3">
        {targeted && <section ref={targetRef} tabIndex={-1} aria-label="Отчёт из истории" className="rounded-md border border-accent-border bg-accent-soft p-3">
            {targetQuery.isPending && <Skeleton className="h-16" />}
            {targetQuery.error && <ErrorMessage message={targetQuery.error.message} action={<Button onClick={() => void targetQuery.refetch()}>Повторить загрузку версии</Button>} />}
            {selectedReport && <><p className="mb-2 text-[12px] font-medium">Версия {selectedReport.revision} из истории</p><p className="whitespace-pre-wrap break-words text-[13px]">{selectedReport.body_md || "Отчёт очищен."}</p></>}
            {targetQuery.data && !selectedReport && <p role="status">Версия отчёта больше недоступна.</p>}
        </section>}
        {targeted && <h4 className="text-[13px] font-semibold">Текущий отчёт</h4>}
        {hasReport
            ? <div className="markdown-body text-[13px]" dangerouslySetInnerHTML={{ __html: reportHtml }} />
            : <p className="text-[13px] text-muted">Отчёт пока не добавлен.</p>}
        {report.data && report.data.revision > 0 && <p className="text-[12px] text-muted">
            Версия {report.data.revision} · {report.data.author_name && <UserIdentity userId={report.data.author_id} name={report.data.author_name} />} · {report.data.created_at ? formatDateTime(report.data.created_at) : ""}
            {report.data.source_comment_original_id && <> · Из комментария #{report.data.source_comment_original_id} (<UserIdentity name={report.data.source_author_name ?? "автор неизвестен"} />)</>}
        </p>}
        {!canEdit && <p className="text-[12px] text-muted">{readOnlyHint ?? "У вас есть доступ к просмотру отчёта."}</p>}
        {canEdit && selectedComment ? <div className="rounded-md border border-line-subtle bg-surface/35 p-3 flex flex-col gap-2">
            <p>Комментарий #{selectedComment.id} выбран как отчёт. Его текст будет скопирован, последующие правки комментария отчёт не изменят.</p>
            <p className="whitespace-pre-wrap text-[13px]">{selectedComment.body_md}</p>
            <div className="flex gap-2">
                <Button disabled={publish.isPending || !report.data} onClick={() => publish.mutate(selectedComment)}>Опубликовать комментарий как отчёт</Button>
                <Button variant="ghost" disabled={publish.isPending} onClick={cancelEditing}>Отмена</Button>
            </div>
        </div> : canEdit && editing ? <div className="flex flex-col gap-3">
            <p className="text-[12px] text-muted">Опишите результат и проверки. Каждая публикация сохраняет полную версию.</p>
            <Textarea aria-label="Отчёт по задаче" placeholder="Что сделано и как проверен результат…" rows={9} maxLength={50000} disabled={publish.isPending} value={draft} onChange={(e) => setDraft(e.target.value)} />
            <div className="flex justify-end gap-2">
                <Button variant="ghost" disabled={publish.isPending} onClick={cancelEditing}>Отмена</Button>
                <Button variant="primary" disabled={draft === (report.data?.body_md ?? "") || publish.isPending || !report.data} onClick={() => publish.mutate(null)}>Опубликовать отчёт</Button>
            </div>
        </div> : canEdit && <div><Button disabled={!report.data} onClick={beginEditing}>{hasReport ? "Изменить отчёт" : "Добавить отчёт"}</Button></div>}
        {publish.error && <div><ErrorMessage message={(publish.error as Error).message} /><Button variant="ghost" onClick={() => { cancelEditing(); void report.refetch(); }}>Сбросить черновик и обновить отчёт</Button></div>}
        {report.data && report.data.revision > 0 && <details onToggle={(e) => setHistoryOpen(e.currentTarget.open)}><summary className="cursor-pointer text-[13px]">История публикаций</summary>
            {history.isPending && <Skeleton className="mt-2 h-16 w-full" />}
            {(history.data ?? []).map((item) => <article key={item.revision} className="rounded-md border border-line-subtle bg-surface/35 mt-2 p-3">
                <p className="text-[12px] text-muted">Версия {item.revision} · {item.author_name && <UserIdentity userId={item.author_id} name={item.author_name} />}</p>
                <p className="whitespace-pre-wrap text-[13px]">{item.body_md || "Отчёт очищен."}</p>
            </article>)}
            {history.error && <ErrorMessage message={(history.error as Error).message} />}
        </details>}
    </div>;
}
