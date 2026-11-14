import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { MessageSquare } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import { fileDraftKey, mergeFileDrafts, type FileUploadDraft } from "@/lib/files";
import { formatRelative } from "@/lib/dates";
import { fullName, type TaskComment } from "@/lib/types";
import { newUuid } from "@/lib/uuid";
import { MAX_DOCUMENT_SIZE, DOCUMENT_UPLOAD_ACCEPT } from "@/lib/documents";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Field";
import { ErrorMessage, Skeleton } from "@/components/ui/States";
import { FileDraftList } from "@/components/files/FileDraftList";
import { DocumentReferenceList } from "@/components/documents/DocumentReferenceList";
import { DocumentList } from "@/components/documents/DocumentList";
import { DocumentPagination } from "@/components/documents/DocumentPagination";
import { useDocuments } from "@/lib/useDocuments";
import { FileUploadControl } from "@/components/files/FileUploadControl";

export function TaskCommentsSection({ taskId, comments, currentUserId, onChange, onReport, loading, error }: {
    taskId: number;
    comments: TaskComment[];
    currentUserId?: number;
    onChange: () => void;
    onReport: (comment: TaskComment) => void;
    loading: boolean;
    error?: string;
}) {
    const client = useQueryClient();
    const composerRef = useRef<HTMLDivElement>(null);
    const request = useRef<{ signature: string; id: string } | null>(null);
    const [body, setBody] = useState("");
    const [drafts, setDrafts] = useState<FileUploadDraft[]>([]);
    const [documentRefs, setDocumentRefs] = useState<string[]>([]);
    const [documentOffset, setDocumentOffset] = useState(0);
    const [fileError, setFileError] = useState<string | null>(null);
    const [editing, setEditing] = useState<TaskComment | null>(null);
    const [editDraft, setEditDraft] = useState("");
    const documents = useDocuments({ task_id: taskId, limit: 20, offset: documentOffset });

    function refresh() {
        void client.invalidateQueries({ queryKey: queryKeys.taskComments(taskId) });
        void client.invalidateQueries({ queryKey: ["documents"] });
        onChange();
    }
    const send = useMutation({
        mutationFn: async () => {
            const signature = JSON.stringify([body, drafts.map(draft => [fileDraftKey(draft.file), draft.caption]), documentRefs]);
            if (request.current?.signature !== signature) request.current = { signature, id: newUuid() };
            const data = new FormData();
            data.append("body_md", body);
            data.append("request_id", request.current.id);
            data.append("document_refs", JSON.stringify(documentRefs));
            data.append("captions", JSON.stringify(drafts.map(draft => draft.caption.trim() || null)));
            for (const draft of drafts) data.append("files", draft.file);
            return api.postForm<TaskComment>(endpoints.taskCommentsWithFiles(taskId), data);
        },
        retry: false,
        onSuccess: () => { setBody(""); setDrafts([]); setDocumentRefs([]); setFileError(null); request.current = null; refresh(); },
    });
    function changePayload(change: () => void) { request.current = null; send.reset(); change(); }
    const edit = useMutation({
        mutationFn: () => api.patch<TaskComment>(`/api/v1/comments/${editing!.id}`, { body_md: editDraft, expected_body_md: editing!.body_md }),
        onSuccess: () => { setEditing(null); refresh(); },
    });
    const remove = useMutation({ mutationFn: (id: number) => api.delete(`/api/v1/comments/${id}`), onSuccess: refresh });
    return <div className="flex min-w-0 flex-col gap-3">
        {loading && <Skeleton className="h-24 w-full" />}
        {error && <ErrorMessage title="Не удалось загрузить комментарии" message={error} action={<Button onClick={() => void client.invalidateQueries({ queryKey: queryKeys.taskComments(taskId) })}>Повторить загрузку комментариев</Button>} />}
        {remove.error && <ErrorMessage title="Не удалось удалить комментарий" message={remove.error.message} />}
        {edit.error && <ErrorMessage title="Не удалось изменить комментарий" message={edit.error.message} />}
        {comments.map(comment => <article key={comment.id} className="min-w-0 rounded-md border-l-2 border-accent-border bg-white/[0.025] px-2.5 py-2">
            <div className="mb-1 flex flex-wrap items-center gap-2 text-[11px] text-muted">
                <MessageSquare size={11} aria-hidden="true" />
                <span className="font-medium text-secondary">{comment.author ? fullName(comment.author) : comment.author_name ?? "Без подписи"}</span>
                <time dateTime={comment.created_at}>{formatRelative(comment.created_at)}</time>
            </div>
            {editing?.id === comment.id ? <div className="flex flex-col gap-2">
                <Textarea aria-label="Изменить комментарий" value={editDraft} disabled={edit.isPending} onChange={event => setEditDraft(event.target.value)} />
                <div className="flex gap-2"><Button size="sm" disabled={!editDraft.trim() || edit.isPending} onClick={() => edit.mutate()}>Сохранить</Button><Button size="sm" variant="ghost" disabled={edit.isPending} onClick={() => setEditing(null)}>Отмена</Button></div>
            </div> : comment.body_md && <p className="text-[13px] break-words whitespace-pre-wrap text-secondary">{comment.body_md}</p>}
            {(comment.document_refs?.length ?? 0) > 0 && <div className="mt-2"><DocumentReferenceList documentRefs={comment.document_refs!} /></div>}
            <div className="mt-2 flex flex-wrap gap-2">
                {(!comment.author || comment.author.id === currentUserId) && <>
                    <Button size="sm" variant="ghost" disabled={edit.isPending} onClick={() => { edit.reset(); setEditing(comment); setEditDraft(comment.body_md); }}>Изменить</Button>
                    <Button size="sm" variant="ghost" title="Документы останутся в задаче" disabled={remove.isPending} onClick={() => remove.mutate(comment.id)}>Удалить</Button>
                </>}
                <Button size="sm" variant="ghost" onClick={() => onReport(comment)}>Считать отчётом</Button>
            </div>
        </article>)}
        {!loading && comments.length === 0 && <p className="text-[13px] text-muted">Комментариев пока нет.</p>}
        <div ref={composerRef} className="flex min-w-0 flex-col gap-3 border-t border-line-subtle pt-3">
            <Textarea rows={3} value={body} disabled={send.isPending} aria-label="Текст комментария" placeholder="Написать комментарий…"
                onChange={event => changePayload(() => setBody(event.target.value))} />
            <FileDraftList drafts={drafts} disabled={send.isPending} onChange={value => changePayload(() => setDrafts(value))} />
            <details className="min-w-0 rounded border border-line-subtle px-2.5 py-2">
                <summary className="cursor-pointer text-[12px] text-secondary">Из документов задачи{documentRefs.length > 0 ? ` · выбрано ${documentRefs.length}` : ""}</summary>
                <div className="mt-3 flex min-w-0 flex-col gap-2">
                    {documents.isPending && <Skeleton className="h-14 w-full" />}
                    {documents.error && <ErrorMessage title="Не удалось загрузить документы задачи" message={documents.error.message} action={<Button onClick={() => void documents.refetch()}>Повторить</Button>} />}
                    {documents.data && <><DocumentList documents={documents.data.items} actions={document => <input type="checkbox" aria-label={`Прикрепить ${document.title}`} checked={documentRefs.includes(document.ref)} disabled={send.isPending || (!documentRefs.includes(document.ref) && documentRefs.length + drafts.length >= 10)} className="m-2 size-4 shrink-0 accent-accent" onChange={event => changePayload(() => setDocumentRefs(current => event.target.checked ? [...current, document.ref] : current.filter(ref => ref !== document.ref)))} />} /><DocumentPagination page={documents.data} busy={documents.isFetching || send.isPending} onOffsetChange={setDocumentOffset} />{documents.data.total === 0 && <p className="text-[12px] text-muted">В задаче пока нет документов.</p>}</>}
                </div>
            </details>
            <p className="text-[11px] text-muted">До 10 документов к комментарию, до 10 МБ каждый.</p>
            <FileUploadControl label="Документы к комментарию" accept={DOCUMENT_UPLOAD_ACCEPT} maxFileSize={MAX_DOCUMENT_SIZE} currentCount={drafts.length} maxFiles={10 - documentRefs.length} uploading={send.isPending} scopeRef={composerRef}
                onFiles={files => changePayload(() => setDrafts(current => mergeFileDrafts(current, files)))} onError={setFileError} />
            {fileError && <ErrorMessage title="Не удалось выбрать файлы" message={fileError} />}
            {send.error && <ErrorMessage title="Комментарий не отправлен" message={`${send.error.message} Черновик сохранён; повторите отправку.`} />}
            <div className="flex justify-end"><Button size="sm" variant="primary" disabled={send.isPending || (!body.trim() && drafts.length === 0 && documentRefs.length === 0)} onClick={() => send.mutate()}>
                {send.isPending ? "Отправка…" : "Отправить"}
            </Button></div>
        </div>
    </div>;
}
