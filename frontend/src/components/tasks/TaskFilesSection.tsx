import { useDeferredValue, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, endpoints, queryKeys } from "@/lib/api";
import { attachmentDescriptor } from "@/lib/fileDescriptors";
import { mergeFileDrafts, type FileDescriptor, type FileUploadDraft } from "@/lib/files";
import type { FileSearchPage, LinkedDocument, TaskAttachment } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";
import { FileList } from "@/components/files/FileList";
import { FileUploadControl } from "@/components/files/FileUploadControl";
import { FileUploadDialog } from "@/components/files/FileUploadDialog";
import { FileSearchResults } from "@/components/files/FileSearchResults";

export function TaskFilesSection({ taskId, attachments, linkedDocuments, projectKey, onChange, loading, error }: {
    taskId: number;
    attachments: TaskAttachment[];
    linkedDocuments: LinkedDocument[];
    projectKey?: string;
    onChange: () => void;
    loading: boolean;
    error?: string;
}) {
    const client = useQueryClient();
    const scopeRef = useRef<HTMLDivElement>(null);
    const [drafts, setDrafts] = useState<FileUploadDraft[]>([]);
    const [fileError, setFileError] = useState<string | null>(null);
    const [search, setSearch] = useState("");
    const [offset, setOffset] = useState(0);
    const [captionFile, setCaptionFile] = useState<FileDescriptor | null>(null);
    const [captionDraft, setCaptionDraft] = useState("");
    const query = useDeferredValue(search.trim());
    const results = useQuery({
        queryKey: queryKeys.taskFilesSearch(taskId, query, offset),
        queryFn: () => api.get<FileSearchPage>(`${endpoints.taskFilesSearch(taskId)}?${new URLSearchParams({ q: query, limit: "20", offset: String(offset) })}`),
        enabled: Boolean(query), retry: false,
    });
    function refresh() {
        void client.invalidateQueries({ queryKey: queryKeys.taskAttachments(taskId) });
        void client.invalidateQueries({ queryKey: queryKeys.taskComments(taskId) });
        void client.invalidateQueries({ queryKey: ["tasks", taskId, "files-search"] });
        onChange();
    }
    const upload = useMutation({
        mutationFn: async (files: FileUploadDraft[]) => {
            const failed: FileUploadDraft[] = [];
            const errors: string[] = [];
            for (const draft of files) {
                const body = new FormData();
                body.append("file", draft.file);
                if (draft.caption.trim()) body.append("caption", draft.caption.trim());
                try { await api.postForm<TaskAttachment>(endpoints.taskAttachments(taskId), body); }
                catch (error) { failed.push(draft); errors.push(`«${draft.file.name}»: ${(error as Error).message}`); }
            }
            return { failed, errors };
        },
        onSuccess: result => { setDrafts(result.failed); refresh(); },
    });
    const remove = useMutation({
        mutationFn: (id: number) => api.delete(endpoints.taskAttachment(taskId, id)),
        onSuccess: refresh,
    });
    const retry = useMutation({ mutationFn: (id: number) => api.post<TaskAttachment>(endpoints.taskAttachmentRetry(taskId, id)), onSuccess: refresh });
    const caption = useMutation({
        mutationFn: () => api.patch<TaskAttachment>(endpoints.taskAttachment(taskId, Number(captionFile!.key)), { caption: captionDraft.trim() || null }),
        onSuccess: () => { setCaptionFile(null); refresh(); },
    });
    return <div ref={scopeRef} className="flex min-w-0 flex-col gap-3">
        <Input aria-label="Поиск в файлах задачи" placeholder="Название, подпись или содержимое" value={search} onChange={event => { setSearch(event.target.value); setOffset(0); }} />
        {error && <ErrorMessage message={error} action={<Button onClick={() => void client.invalidateQueries({ queryKey: queryKeys.taskAttachments(taskId) })}>Повторить загрузку файлов</Button>} />}
        {fileError && <ErrorMessage message={fileError} />}
        {remove.error && <ErrorMessage title="Не удалось удалить файл" message={remove.error.message} />}
        {retry.error && <ErrorMessage title="Не удалось повторить обработку" message={retry.error.message} />}
        {query ? <>
            {results.isPending && <Skeleton className="h-24 w-full" />}
            {results.error && <ErrorMessage title="Не удалось найти файлы" message={results.error.message} action={<Button onClick={() => void results.refetch()}>Повторить поиск</Button>} />}
            {results.data && <>
                <p role="status" className="text-[12px] text-muted">Найдено: {results.data.total}</p>
                {results.data.items.length ? <FileSearchResults items={results.data.items} projectKey={projectKey} /> : <EmptyState title="Файлы не найдены" description="Измените поисковый запрос." />}
                {results.data.total > 20 && <nav aria-label="Страницы файлов задачи" className="flex gap-2">
                    <Button disabled={offset === 0 || results.isFetching} onClick={() => setOffset(value => Math.max(0, value - 20))}>Назад</Button>
                    <Button disabled={offset + 20 >= results.data.total || results.isFetching} onClick={() => setOffset(value => value + 20)}>Далее</Button>
                </nav>}
            </>}
        </> : <>
            {loading && <Skeleton className="h-20 w-full" />}
            {!loading && attachments.length === 0 && <p className="text-[13px] text-muted">Файлов пока нет.</p>}
            <FileList files={attachments.map(attachmentDescriptor)} onRemove={file => remove.mutate(Number(file.key))} removeDisabled={remove.isPending || retry.isPending}
                onRetry={file => retry.mutate(Number(file.key))} retryDisabled={retry.isPending || remove.isPending}
                onEditCaption={file => { caption.reset(); setCaptionFile(file); setCaptionDraft(file.caption ?? ""); }} />
            {projectKey && linkedDocuments.length > 0 && <div className="flex flex-col gap-1 text-[12px]">
                <span className="text-muted">Связанные документы</span>
                {linkedDocuments.map(doc => <Link key={doc.link_id} to={`/projects/${projectKey}/docs/${doc.slug}`} className="touch-target truncate rounded text-accent hover:underline">{doc.title}</Link>)}
            </div>}
        </>}
        <FileUploadControl onFiles={files => { upload.reset(); setDrafts(current => mergeFileDrafts(current, files)); }} onError={setFileError}
            currentCount={attachments.length + drafts.length} uploading={upload.isPending} scopeRef={scopeRef} />
        <FileUploadDialog drafts={drafts} onChange={setDrafts} busy={upload.isPending} error={upload.data?.errors.join(" ")}
            onClose={() => setDrafts([])} onUpload={() => upload.mutate(drafts)} />
        <Modal title="Подпись к файлу" isOpen={captionFile !== null} isDismissable={!caption.isPending} onOpenChange={open => { if (!open && !caption.isPending) setCaptionFile(null); }}
            footer={<><Button disabled={caption.isPending} onClick={() => setCaptionFile(null)}>Отмена</Button><Button variant="primary" disabled={caption.isPending} onClick={() => caption.mutate()}>Сохранить подпись</Button></>}>
            <div className="flex flex-col gap-3">
                <p className="break-words text-[13px] text-muted">{captionFile?.name}</p>
                <Input aria-label="Подпись к файлу" maxLength={2000} value={captionDraft} disabled={caption.isPending} onChange={event => setCaptionDraft(event.target.value)} />
                {caption.error && <ErrorMessage message={caption.error.message} />}
            </div>
        </Modal>
    </div>;
}
