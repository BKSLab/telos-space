import { useDeferredValue, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Unlink } from "lucide-react";
import { api, endpoints } from "@/lib/api";
import { createMarkdownDocument, DOCUMENT_PAGE_SIZE, uploadDocument } from "@/lib/documents";
import { useDocuments, useRefreshDocuments } from "@/lib/useDocuments";
import { DocumentAddButton, DocumentComposeDialog } from "@/components/documents/DocumentComposeDialog";
import { DocumentList } from "@/components/documents/DocumentList";
import { DocumentPagination } from "@/components/documents/DocumentPagination";
import { Button, IconButton } from "@/components/ui/Button";
import { Input, Select } from "@/components/ui/Field";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";

export function TaskFilesSection({ taskId, projectId, projectKey, onChange }: { taskId: number; projectId?: number; projectKey?: string; onChange: () => void }) {
    const refreshDocuments = useRefreshDocuments();
    const [search, setSearch] = useState("");
    const [kind, setKind] = useState<"" | "text" | "upload">("");
    const [processing, setProcessing] = useState("");
    const [offset, setOffset] = useState(0);
    const [adding, setAdding] = useState(false);
    const q = useDeferredValue(search.trim());
    const documents = useDocuments({ task_id: taskId, q, source_kind: kind || undefined, processing, limit: DOCUMENT_PAGE_SIZE, offset });
    function refresh() { refreshDocuments(); onChange(); }
    const detach = useMutation({ mutationFn: (ref: string) => api.delete(endpoints.documentTask(ref, taskId)), onSuccess: refresh });
    return <div className="flex min-w-0 flex-col gap-3">
        <div className="flex min-w-0 flex-wrap gap-2">
            <Input aria-label="Поиск документов задачи" placeholder="Название, подпись или содержимое" value={search} className="min-w-0 basis-full sm:flex-1 sm:basis-auto" onChange={event => { setSearch(event.target.value); setOffset(0); }} />
            <DocumentAddButton onClick={() => setAdding(true)} />
        </div>
        <div className="grid min-w-0 gap-2 sm:grid-cols-2">
            <Select aria-label="Тип документа задачи" value={kind} onChange={event => { setKind(event.target.value as typeof kind); setOffset(0); }}><option value="">Все типы</option><option value="text">Markdown</option><option value="upload">Загруженные</option></Select>
            <Select aria-label="Обработка документов задачи" value={processing} onChange={event => { setProcessing(event.target.value); setOffset(0); }}><option value="">Любое состояние</option><option value="pending">Обрабатывается</option><option value="ready">Готово</option><option value="failed">Ошибка</option></Select>
        </div>
        {documents.isPending && <Skeleton className="h-24 w-full" />}
        {documents.error && <ErrorMessage title="Не удалось загрузить документы задачи" message={documents.error.message} action={<Button onClick={() => void documents.refetch()}>Повторить загрузку</Button>} />}
        {detach.error && <ErrorMessage title="Не удалось убрать связь с задачей" message={detach.error.message} />}
        {documents.data && <>
            {documents.data.items.length ? <DocumentList documents={documents.data.items} projectKey={projectKey} actions={document => document.capabilities.can_detach && document.tasks.some(task => task.id === taskId && task.relation === "linked") ? <IconButton label={`Убрать связь с документом ${document.title}`} disabled={detach.isPending} onClick={() => detach.mutate(document.ref)}><Unlink size={13} /></IconButton> : null} /> : <EmptyState title={q || kind || processing ? "Документы не найдены" : "Документов пока нет"} description={q || kind || processing ? "Измените запрос или фильтры." : "Создайте Markdown, загрузите документ или выберите из проекта."} />}
            <DocumentPagination page={documents.data} busy={documents.isFetching} onOffsetChange={setOffset} />
        </>}
        <DocumentComposeDialog projectId={projectId} selectedRefs={documents.data?.items.map(document => document.ref)} isOpen={adding} onClose={() => setAdding(false)}
            onMarkdown={async (draft, id) => { await createMarkdownDocument({ taskId }, draft, id); refresh(); }}
            onUpload={async (draft, id) => { await uploadDocument({ taskId }, draft.file, draft.caption, id); refresh(); }}
            onExisting={projectId !== undefined ? async selected => { for (const document of selected) await api.post(endpoints.documentTask(document.ref, taskId)); refresh(); } : undefined} />
    </div>;
}
