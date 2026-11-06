import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Download, Paperclip, RotateCw, Trash2 } from "lucide-react";
import { api, apiUrl, endpoints, queryKeys } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatRelative } from "@/lib/dates";
import { formatFileSize } from "@/lib/files";
import { EXTRACTION_LABELS, INDEX_LABELS, isProjectFileProcessing, PROJECT_FILE_TEXT_PAGE_SIZE, projectFilesPollInterval } from "@/lib/projectFiles";
import type { ProjectFile, ProjectFileDetail } from "@/lib/types";
import type { useProjectFiles } from "@/lib/useProjectFiles";
import { Button, IconButton } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Modal } from "@/components/ui/Modal";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";

type FilesModel = ReturnType<typeof useProjectFiles>;

export function ProjectFilesSection({
    projectId, model, search, selectedFileId, onOpenFile, onCloseFile,
}: {
    projectId: number;
    model: FilesModel;
    search: string;
    selectedFileId: number | null;
    onOpenFile: (id: number) => void;
    onCloseFile: () => void;
}) {
    const [deleteTarget, setDeleteTarget] = useState<ProjectFile | null>(null);
    const files = model.files.data?.pages.flat() ?? [];

    function confirmDelete(file: ProjectFile) {
        model.remove.reset();
        setDeleteTarget(file);
    }

    async function removeFile() {
        if (!deleteTarget) return;
        try {
            await model.remove.mutateAsync(deleteTarget.id);
            setDeleteTarget(null);
            if (selectedFileId === deleteTarget.id) onCloseFile();
        } catch { /* Ошибка остаётся в диалоге; повтор доступен явно. */ }
    }

    return (
        <section aria-label="Файлы проекта" className="flex min-w-0 flex-col gap-3">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <h2 className="text-sm font-semibold text-primary">Файлы проекта</h2>
                <p className="text-[12px] text-muted">PDF, DOCX, Excel, текст и изображения · до 10 МБ, до 10 за раз</p>
            </div>

            {model.upload.isPending && <p role="status" className="text-[13px] text-secondary">Загружаем файлы…</p>}
            {model.upload.data && model.upload.data.uploaded > 0 && (
                <p role="status" className="text-[13px] text-secondary">Загружено файлов: {model.upload.data.uploaded}</p>
            )}
            {model.upload.data && model.upload.data.errors.length > 0 && (
                <ErrorMessage title="Не удалось загрузить файлы" message={model.upload.data.errors.join(" ")} />
            )}
            {model.files.error && (
                <ErrorMessage message={model.files.error.message} action={
                    <Button disabled={model.files.isFetching} onClick={() => void model.files.refetch()}>Повторить загрузку файлов</Button>
                } />
            )}
            {model.retry.error && <ErrorMessage title="Не удалось повторить обработку" message={model.retry.error.message} />}
            {model.files.isPending && (
                <div role="status" aria-label="Загрузка файлов проекта" className="flex flex-col gap-2">
                    <Skeleton className="h-24 w-full" />
                    <Skeleton className="h-24 w-full" />
                </div>
            )}
            {!model.files.isPending && !model.files.error && files.length === 0 && (
                <EmptyState
                    title={search ? "Файлы не найдены" : "Файлов пока нет"}
                    description={search ? "Измените поисковый запрос." : "Загрузите материалы проекта. Оригинал и извлечённый текст будут доступны здесь."}
                    icon={<Paperclip size={24} />}
                    className="py-6"
                />
            )}
            {files.length > 0 && (
                <ul className="flex min-w-0 flex-col gap-2">
                    {files.map((file) => (
                        <li key={file.id}>
                            <Card className="flex min-w-0 flex-wrap items-start gap-2 px-3 py-3">
                                <button
                                    type="button"
                                    aria-label={`Открыть файл ${file.original_name}`}
                                    className="touch-target min-w-0 flex-1 rounded text-left focus-visible:outline-2 focus-visible:outline-accent"
                                    onClick={() => onOpenFile(file.id)}
                                >
                                    <span className="flex min-w-0 items-start gap-2">
                                        <Paperclip size={15} className="mt-0.5 shrink-0 text-muted" aria-hidden="true" />
                                        <span className="min-w-0 flex-1">
                                            <span className="block text-[14px] font-medium break-words text-primary [overflow-wrap:anywhere]">{file.original_name}</span>
                                            <span className="mt-0.5 block text-[11px] text-muted">{formatFileSize(file.size)} · Загружен {formatRelative(file.created_at)}</span>
                                        </span>
                                    </span>
                                    <FileStatus file={file} />
                                    {file.summary && <span className="mt-2 line-clamp-2 block text-[12px] break-words text-secondary">{file.summary}</span>}
                                </button>
                                <div className="flex shrink-0 items-center gap-1">
                                    <a
                                        href={apiUrl(endpoints.projectFileContent(projectId, file.id))}
                                        download={file.original_name}
                                        aria-label={`Скачать ${file.original_name}`}
                                        title="Скачать оригинал"
                                        className="touch-target flex h-8 w-8 items-center justify-center rounded-[var(--radius-control)] text-secondary hover:bg-hover hover:text-primary focus-visible:outline-2 focus-visible:outline-accent"
                                    ><Download size={15} aria-hidden="true" /></a>
                                    <IconButton label={`Повторить обработку ${file.original_name}`} disabled={isProjectFileProcessing(file) || model.retry.isPending} onClick={() => model.retry.mutate(file.id)}>
                                        <RotateCw size={15} aria-hidden="true" />
                                    </IconButton>
                                    <IconButton label={`Удалить файл ${file.original_name}`} onClick={() => confirmDelete(file)}>
                                        <Trash2 size={15} aria-hidden="true" />
                                    </IconButton>
                                </div>
                            </Card>
                        </li>
                    ))}
                </ul>
            )}
            {model.files.hasNextPage && (
                <Button className="self-start" disabled={model.files.isFetching} onClick={() => void model.files.fetchNextPage()}>
                    {model.files.isFetchingNextPage ? "Загрузка…" : "Показать ещё файлы"}
                </Button>
            )}

            {selectedFileId !== null && (
                <ProjectFileDialog
                    key={`${projectId}:${selectedFileId}`}
                    projectId={projectId}
                    fileId={selectedFileId}
                    onClose={onCloseFile}
                    onDelete={confirmDelete}
                    retry={model.retry}
                />
            )}
            <Modal
                title="Удалить файл проекта?"
                isOpen={deleteTarget !== null}
                isDismissable={!model.remove.isPending}
                onOpenChange={(open) => { if (!open && !model.remove.isPending) setDeleteTarget(null); }}
                footer={<>
                    <Button disabled={model.remove.isPending} onClick={() => setDeleteTarget(null)}>Отмена</Button>
                    <Button variant="destructive" disabled={model.remove.isPending} onClick={() => void removeFile()}>
                        {model.remove.isPending ? "Удаление…" : "Удалить файл"}
                    </Button>
                </>}
            >
                <div className="flex flex-col gap-3">
                    <p className="text-[13px] break-words text-secondary [overflow-wrap:anywhere]">«{deleteTarget?.original_name}» будет удалён вместе с извлечённым текстом и знаниями Ариса об этом файле. Восстановить его можно будет только повторной загрузкой.</p>
                    {model.remove.error && <ErrorMessage title="Не удалось удалить файл" message={model.remove.error.message} />}
                </div>
            </Modal>
        </section>
    );
}

function FileStatus({ file }: { file: ProjectFile }) {
    return (
        <span className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[12px]">
            <span className={cn(file.extraction_status === "failed" ? "text-danger" : "text-secondary")}>
                {file.extraction_status === "pending" && file.index_status === "disabled" ? "Извлечение отключено" : EXTRACTION_LABELS[file.extraction_status]}
            </span>
            <span className={cn(file.index_status === "failed" ? "text-danger" : "text-muted")}>
                {INDEX_LABELS[file.index_status]}
            </span>
        </span>
    );
}

function ProjectFileDialog({ projectId, fileId, onClose, onDelete, retry }: {
    projectId: number;
    fileId: number;
    onClose: () => void;
    onDelete: (file: ProjectFile) => void;
    retry: FilesModel["retry"];
}) {
    const [offsets, setOffsets] = useState([0]);
    const offset = offsets[offsets.length - 1];
    const detail = useQuery({
        queryKey: queryKeys.projectFile(projectId, fileId, offset),
        queryFn: () => api.get<ProjectFileDetail>(`${endpoints.projectFile(projectId, fileId)}?offset=${offset}&max_chars=${PROJECT_FILE_TEXT_PAGE_SIZE}`),
        retry: false,
        refetchInterval: (query) => projectFilesPollInterval(query.state.data ? [query.state.data] : undefined, query.state.status === "error"),
    });
    const file = detail.data;

    return (
        <Modal
            isOpen
            title={file?.original_name ?? "Файл проекта"}
            description={file ? `${formatFileSize(file.size)} · Загружен ${formatRelative(file.created_at)}` : undefined}
            size="lg"
            layout="workspace"
            onOpenChange={(open) => { if (!open) onClose(); }}
            footer={<>
                {file && <>
                    <Button variant="destructive" icon={<Trash2 size={14} />} onClick={() => onDelete(file)}>Удалить</Button>
                    <Button
                        icon={<RotateCw size={14} />}
                        disabled={isProjectFileProcessing(file) || retry.isPending}
                        onClick={() => { setOffsets([0]); retry.mutate(file.id); }}
                    >Повторить обработку</Button>
                </>}
                <a
                    href={apiUrl(endpoints.projectFileContent(projectId, fileId))}
                    download={file?.original_name}
                    className="touch-target material-metal inline-flex h-8 items-center justify-center gap-2 rounded-[var(--radius-control)] border border-line px-3 text-[13px] font-medium text-primary hover:bg-hover focus-visible:outline-2 focus-visible:outline-accent"
                ><Download size={14} aria-hidden="true" />Скачать оригинал</a>
            </>}
        >
            <div className="scrollbar-thin flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-4 py-4 sm:px-5">
                {detail.isPending && <div role="status" aria-label="Загрузка текста файла"><Skeleton className="h-52 w-full" /></div>}
                {detail.error && <ErrorMessage message={detail.error.message} action={
                    <Button disabled={detail.isFetching} onClick={() => void detail.refetch()}>Повторить загрузку текста</Button>
                } />}
                {file && <>
                    <div>
                        <FileStatus file={file} />
                        {file.extraction_detail && <p className="mt-2 text-[13px] break-words text-secondary">{file.extraction_detail}</p>}
                        {file.index_detail && <p className="mt-1 text-[13px] break-words text-secondary">{file.index_detail}</p>}
                        {retry.error && <ErrorMessage title="Не удалось повторить обработку" message={retry.error.message} className="mt-3" />}
                    </div>
                    <h3 className="text-sm font-semibold text-primary">Извлечённый текст</h3>
                    {file.text_total_chars > 0 ? <>
                        <nav aria-label="Страницы текста" className="flex flex-wrap items-center gap-2">
                            <Button icon={<ChevronLeft size={14} />} disabled={offsets.length === 1 || detail.isFetching} onClick={() => setOffsets((current) => current.slice(0, -1))}>Назад</Button>
                            <span className="text-[12px] text-muted">{file.text_offset + 1}–{file.next_offset ?? file.text_total_chars} из {file.text_total_chars}</span>
                            <Button icon={<ChevronRight size={14} />} disabled={file.next_offset === null || detail.isFetching} onClick={() => { const next = file.next_offset; if (next !== null) setOffsets((current) => [...current, next]); }}>Далее</Button>
                        </nav>
                        <pre aria-label="Извлечённый текст" tabIndex={0} className="whitespace-pre-wrap rounded-[var(--radius-control)] bg-app/60 px-3 py-3 font-sans text-[13px] leading-relaxed break-words text-primary [overflow-wrap:anywhere] focus-visible:outline-2 focus-visible:outline-accent">{file.text}</pre>
                    </> : <p role="status" className="text-[13px] text-muted">
                        {file.extraction_status === "pending" && file.index_status === "disabled"
                            ? "Обработка файла отключена. Оригинал доступен для скачивания."
                            : isProjectFileProcessing(file) ? "Файл обрабатывается. Текст появится автоматически." : "Извлечённого текста нет. Вы можете скачать оригинал или повторить обработку."}
                    </p>}
                </>}
            </div>
        </Modal>
    );
}
