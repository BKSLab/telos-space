import { useState } from "react";
import { AlertTriangle, LoaderCircle, Paperclip } from "lucide-react";
import { formatFullDate } from "@/lib/dates";
import { projectFileDescriptor } from "@/lib/fileDescriptors";
import { fileTypeLabel } from "@/lib/files";
import { EXTRACTION_LABELS, INDEX_LABELS, isProjectFileProcessing } from "@/lib/projectFiles";
import type { ProjectFile } from "@/lib/types";
import type { useProjectFiles } from "@/lib/useProjectFiles";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";
import { FileTypeIcon } from "./FileTypeIcon";
import { ProjectFileDialog } from "./ProjectFileDialog";

type FilesModel = ReturnType<typeof useProjectFiles>;

export function ProjectFilesSection({ projectId, model, search, selectedFileId, onOpenFile, onCloseFile, hidden = false }: {
    projectId: number;
    model: FilesModel;
    search: string;
    selectedFileId: number | null;
    onOpenFile: (id: number) => void;
    onCloseFile: () => void;
    hidden?: boolean;
}) {
    const [deleteTarget, setDeleteTarget] = useState<ProjectFile | null>(null);
    const files = model.files.data?.pages.flat() ?? [];

    async function removeFile() {
        if (!deleteTarget) return;
        try {
            await model.remove.mutateAsync(deleteTarget.id);
            setDeleteTarget(null);
            if (selectedFileId === deleteTarget.id) onCloseFile();
        } catch { /* Ошибка остаётся в диалоге подтверждения. */ }
    }

    return <>
        <section hidden={hidden} aria-label="Файлы проекта" className="flex min-w-0 flex-col gap-3 [hidden]:hidden">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <h2 className="text-sm font-semibold text-primary">Файлы проекта</h2>
                <p className="text-[12px] text-muted">PDF, DOCX, Excel, текст и изображения · до 10 МБ, до 10 за раз</p>
            </div>
            {model.upload.isPending && <p role="status" className="text-[13px] text-secondary">Загружаем файлы…</p>}
            {model.upload.data && model.upload.data.uploaded > 0 && <p role="status" className="text-[13px] text-secondary">Загружено файлов: {model.upload.data.uploaded}</p>}
            {!!model.upload.data?.errors.length && <ErrorMessage title="Не удалось загрузить файлы" message={model.upload.data.errors.join(" ")} />}
            {model.files.error && <ErrorMessage message={model.files.error.message} action={
                <Button disabled={model.files.isFetching} onClick={() => void model.files.refetch()}>Повторить загрузку файлов</Button>
            } />}
            {model.files.isPending && <div role="status" aria-label="Загрузка файлов проекта" className="flex flex-col gap-1"><Skeleton className="h-14 w-full" /><Skeleton className="h-14 w-full" /></div>}
            {!model.files.isPending && !model.files.error && files.length === 0 && <EmptyState title={search ? "Файлы не найдены" : "Файлов пока нет"}
                description={search ? "Измените поисковый запрос." : "Загрузите материалы проекта. Оригинал и извлечённый текст будут доступны здесь."} icon={<Paperclip size={24} />} className="py-6" />}
            {files.length > 0 && <ul aria-label="Файлы проекта" className="min-w-0 divide-y divide-line-subtle overflow-hidden rounded-[var(--radius-card)] border border-line-subtle bg-surface/30">
                {files.map(file => {
                    const descriptor = projectFileDescriptor(file);
                    const failed = file.extraction_status === "failed" || file.index_status === "failed" || file.fts_status === "failed";
                    const processing = isProjectFileProcessing(file);
                    const status = failed ? file.extraction_status === "failed" ? EXTRACTION_LABELS.failed : file.fts_status === "failed" ? "Поиск временно недоступен" : INDEX_LABELS.failed
                        : processing ? ["pending", "processing"].includes(file.extraction_status) ? EXTRACTION_LABELS[file.extraction_status]
                            : file.fts_status === "pending" ? "Готовим для поиска" : INDEX_LABELS[file.index_status] : null;
                    return <li key={file.id} className="min-w-0">
                        <button type="button" aria-label={`Открыть файл ${file.original_name}`} onClick={() => onOpenFile(file.id)}
                            className="group flex min-h-14 w-full min-w-0 items-center gap-3 px-3 py-2 text-left hover:bg-hover focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent">
                            <FileTypeIcon file={descriptor} className="h-9 w-9" />
                            <span className="flex min-w-0 flex-1 flex-col gap-1 sm:flex-row sm:items-center sm:gap-4">
                                <span title={file.original_name} className="min-w-0 flex-1 truncate text-[13px] font-medium text-primary">{file.original_name}</span>
                                <span className="flex shrink-0 items-center gap-3 text-[11px] text-muted">
                                    <span className="w-10">{fileTypeLabel(descriptor)}</span>
                                    <time dateTime={file.created_at} className="sm:w-36">{formatFullDate(file.created_at)}</time>
                                </span>
                            </span>
                            <span className="flex w-4 shrink-0 items-center justify-center">
                                {status && <span role="status" aria-label={status} title={status} className={failed ? "text-warning" : "text-muted"}>
                                    {failed ? <AlertTriangle size={15} aria-hidden="true" /> : <LoaderCircle size={15} aria-hidden="true" className="motion-safe:animate-spin" />}
                                </span>}
                            </span>
                        </button>
                    </li>;
                })}
            </ul>}
            {model.files.hasNextPage && <Button className="self-start" disabled={model.files.isFetching} onClick={() => void model.files.fetchNextPage()}>{model.files.isFetchingNextPage ? "Загрузка…" : "Показать ещё файлы"}</Button>}
        </section>
        {selectedFileId !== null && <ProjectFileDialog key={`${projectId}:${selectedFileId}`} projectId={projectId} fileId={selectedFileId}
            onClose={onCloseFile} onDelete={file => { model.remove.reset(); setDeleteTarget(file); }} retry={model.retry} captionMutation={model.caption} />}
        <Modal title="Удалить файл проекта?" isOpen={deleteTarget !== null} isDismissable={!model.remove.isPending}
            onOpenChange={open => { if (!open && !model.remove.isPending) setDeleteTarget(null); }}
            footer={<>
                <Button disabled={model.remove.isPending} onClick={() => setDeleteTarget(null)}>Отмена</Button>
                <Button variant="destructive" disabled={model.remove.isPending} onClick={() => void removeFile()}>{model.remove.isPending ? "Удаление…" : "Удалить файл"}</Button>
            </>}
        >
            <div className="flex flex-col gap-3">
                <p className="text-[13px] break-words text-secondary [overflow-wrap:anywhere]">«{deleteTarget?.original_name}» будет удалён вместе с извлечённым текстом и знаниями Ариса об этом файле. Восстановить его можно будет только повторной загрузкой.</p>
                {model.remove.error && <ErrorMessage title="Не удалось удалить файл" message={model.remove.error.message} />}
            </div>
        </Modal>
    </>;
}
