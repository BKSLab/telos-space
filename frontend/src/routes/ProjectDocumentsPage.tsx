import { useDeferredValue, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { FileText, Search, X } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import { createMarkdownDocument, DOCUMENT_PAGE_SIZE, uploadDocument } from "@/lib/documents";
import type { Task } from "@/lib/types";
import { useDocuments, useRefreshDocuments } from "@/lib/useDocuments";
import { useProjectOutlet } from "@/lib/useProjectOutlet";
import { DocumentAddButton, DocumentComposeDialog } from "@/components/documents/DocumentComposeDialog";
import { DocumentList } from "@/components/documents/DocumentList";
import { DocumentPagination } from "@/components/documents/DocumentPagination";
import { DocumentDialog } from "@/components/documents/DocumentView";
import { Button } from "@/components/ui/Button";
import { Input, Select } from "@/components/ui/Field";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";

export function ProjectDocumentsPage() {
    const project = useProjectOutlet();
    const refresh = useRefreshDocuments();
    const [params, setParams] = useSearchParams();
    const [search, setSearch] = useState("");
    const [scope, setScope] = useState<"all" | "project" | "tasks">("all");
    const [taskId, setTaskId] = useState("");
    const [kind, setKind] = useState<"" | "text" | "upload">("");
    const [format, setFormat] = useState("");
    const [processing, setProcessing] = useState("");
    const [offset, setOffset] = useState(0);
    const [adding, setAdding] = useState(false);
    const q = useDeferredValue(search.trim());
    const selectedRef = params.get("document");
    const documents = useDocuments({ project_id: project.id, q, scope, task_id: taskId ? Number(taskId) : undefined, source_kind: kind || undefined, format, processing, limit: DOCUMENT_PAGE_SIZE, offset });
    const tasks = useQuery({ queryKey: queryKeys.tasks(project.id), queryFn: () => api.get<Task[]>(endpoints.projectTasks(project.id)) });
    const filtered = !!(search.trim() || scope !== "all" || taskId || kind || format || processing);
    function selectDocument(ref: string | null) {
        setParams(previous => { const next = new URLSearchParams(previous); if (ref) next.set("document", ref); else next.delete("document"); return next; }, { replace: ref === null });
    }
    function resetFilters() { setSearch(""); setScope("all"); setTaskId(""); setKind(""); setFormat(""); setProcessing(""); setOffset(0); }
    return <div className="scrollbar-thin h-full overflow-y-auto">
        <div className="flex min-w-0 flex-col gap-4 px-3 py-4 pb-24 sm:px-5 sm:py-5">
            <div className="material-metal flex min-w-0 flex-wrap items-center gap-2 rounded-[var(--radius-card)] border border-line-subtle p-2 shadow-card">
                <div className="relative min-w-0 basis-full sm:max-w-md sm:flex-1 sm:basis-auto">
                    <Search size={14} aria-hidden="true" className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-disabled" />
                    <Input aria-label="Поиск документов проекта" placeholder="Название, подпись или содержимое" className="pl-8" value={search} onChange={event => { setSearch(event.target.value); setOffset(0); }} />
                </div>
                <DocumentAddButton onClick={() => setAdding(true)} />
            </div>
            <div className="grid min-w-0 gap-2 sm:grid-cols-2 xl:grid-cols-5">
                <Select aria-label="Принадлежность документа" value={scope} onChange={event => { const next = event.target.value as typeof scope; setScope(next); if (next === "project") setTaskId(""); setOffset(0); }}>
                    <option value="all">Все документы</option><option value="project">Только проект</option><option value="tasks">Документы задач</option>
                </Select>
                <Select aria-label="Задача документа" value={taskId} disabled={scope === "project" || tasks.isPending} onChange={event => { setTaskId(event.target.value); setOffset(0); }}>
                    <option value="">Все задачи</option>{tasks.data?.map(task => <option key={task.id} value={task.id}>{task.key} · {task.title}</option>)}
                </Select>
                <Select aria-label="Тип документа" value={kind} onChange={event => { setKind(event.target.value as typeof kind); setOffset(0); }}>
                    <option value="">Все типы</option><option value="text">Markdown</option><option value="upload">Загруженные</option>
                </Select>
                <Select aria-label="Формат документа" value={format} onChange={event => { setFormat(event.target.value); setOffset(0); }}>
                    <option value="">Все форматы</option>{["md", "pdf", "docx", "txt", "csv", "xlsx", "xls", "xlsm", "png", "jpg", "jpeg", "webp", "gif", "avif", "bmp", "log"].map(value => <option key={value} value={value}>{value.toUpperCase()}</option>)}
                </Select>
                <Select aria-label="Состояние обработки" value={processing} onChange={event => { setProcessing(event.target.value); setOffset(0); }}>
                    <option value="">Любое состояние</option><option value="pending">Обрабатывается</option><option value="ready">Готово</option><option value="failed">Ошибка</option>
                </Select>
            </div>
            {tasks.error && <ErrorMessage title="Не удалось загрузить список задач" message={tasks.error.message} action={<Button onClick={() => void tasks.refetch()}>Повторить</Button>} />}
            <div className="flex flex-wrap items-center justify-between gap-2">
                <h1 className="text-sm font-semibold text-primary">Документы{documents.data ? <span className="ml-2 text-[12px] font-normal text-muted">{documents.data.total}</span> : ""}</h1>
                {filtered && <Button size="sm" variant="ghost" icon={<X size={13} />} onClick={resetFilters}>Сбросить фильтры</Button>}
            </div>
            {documents.isPending && <div role="status" aria-label="Загрузка документов" className="flex flex-col gap-2">{[0, 1, 2].map(index => <Skeleton key={index} className="h-14 w-full" />)}</div>}
            {documents.error && <ErrorMessage title="Не удалось загрузить документы" message={documents.error.message} action={<Button onClick={() => void documents.refetch()}>Повторить загрузку</Button>} />}
            {documents.data && <>
                {documents.data.items.length > 0 ? <DocumentList documents={documents.data.items} onOpen={document => selectDocument(document.ref)} /> : <EmptyState title={filtered ? "Документы не найдены" : "Документов пока нет"} description={filtered ? "Измените запрос или фильтры." : "Создайте Markdown или загрузите требования, план и решения. Документы задач появятся здесь же."} icon={<FileText size={24} />} action={filtered ? <Button onClick={resetFilters}>Сбросить фильтры</Button> : <DocumentAddButton onClick={() => setAdding(true)} />} />}
                <DocumentPagination page={documents.data} busy={documents.isFetching} onOffsetChange={setOffset} />
            </>}
        </div>
        {selectedRef && <DocumentDialog key={selectedRef} documentRef={selectedRef} projectKey={project.key} expectedProjectId={project.id} onClose={() => selectDocument(null)} />}
        <DocumentComposeDialog projectId={project.id} isOpen={adding} onClose={() => setAdding(false)}
            onMarkdown={async (draft, id) => { const created = await createMarkdownDocument({ projectId: project.id }, draft, id); refresh(); selectDocument(created.ref); }}
            onUpload={async (draft, id) => { await uploadDocument({ projectId: project.id }, draft.file, draft.caption, id); refresh(); }} />
    </div>;
}
