import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Download, Pencil, RotateCw, Trash2 } from "lucide-react";
import { Tab, TabList, TabPanel, Tabs } from "react-aria-components";
import { api, apiUrl, ApiError, endpoints, queryKeys } from "@/lib/api";
import { cn } from "@/lib/cn";
import { projectFileDescriptor } from "@/lib/fileDescriptors";
import { fileTypeLabel, formatFileSize } from "@/lib/files";
import { EXTRACTION_LABELS, INDEX_LABELS, isProjectFileProcessing, PROJECT_FILE_TEXT_PAGE_SIZE, projectFilesPollInterval } from "@/lib/projectFiles";
import type { ProjectFile, ProjectFileDetail } from "@/lib/types";
import type { useProjectFiles } from "@/lib/useProjectFiles";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { ErrorMessage, Skeleton } from "@/components/ui/States";
import { FileTypeIcon } from "./FileTypeIcon";
import { ProjectFileOriginal } from "./ProjectFileOriginal";

type FilesModel = ReturnType<typeof useProjectFiles>;
const EXACT_DATE = new Intl.DateTimeFormat("ru-RU", {
    dateStyle: "long", timeStyle: "long",
});
const PANEL_CLASS = "scrollbar-thin min-h-0 min-w-0 flex-1 overflow-y-auto p-4 outline-none data-[inert]:hidden data-[focus-visible]:outline-2 data-[focus-visible]:-outline-offset-2 data-[focus-visible]:outline-accent sm:p-5";

export function ProjectFileDialog({ projectId, fileId, onClose, onDelete, retry, captionMutation }: {
    projectId: number;
    fileId: number;
    onClose: () => void;
    onDelete: (file: ProjectFile) => void;
    retry: FilesModel["retry"];
    captionMutation: FilesModel["caption"];
}) {
    const [tab, setTab] = useState("document");
    const [offsets, setOffsets] = useState([0]);
    const [captionDraft, setCaptionDraft] = useState("");
    const [editingCaption, setEditingCaption] = useState(false);
    const offset = offsets[offsets.length - 1];
    const detail = useQuery({
        queryKey: queryKeys.projectFile(projectId, fileId, 0),
        queryFn: () => api.get<ProjectFileDetail>(`${endpoints.projectFile(projectId, fileId)}?offset=0&max_chars=${PROJECT_FILE_TEXT_PAGE_SIZE}`),
        retry: false,
        refetchInterval: (query) => projectFilesPollInterval(query.state.data ? [query.state.data] : undefined, query.state.status === "error"),
    });
    const page = useQuery({
        queryKey: queryKeys.projectFile(projectId, fileId, offset),
        queryFn: () => api.get<ProjectFileDetail>(`${endpoints.projectFile(projectId, fileId)}?offset=${offset}&max_chars=${PROJECT_FILE_TEXT_PAGE_SIZE}`),
        enabled: tab === "text" && offset !== 0,
        retry: false,
    });
    const textQuery = offset === 0 ? detail : page;
    const inaccessible = [detail.error, textQuery.error].some(error => error instanceof ApiError && [401, 403, 404].includes(error.status));
    const file = inaccessible ? undefined : detail.data;
    const textPage = textQuery.data;
    const descriptor = file ? projectFileDescriptor(file) : undefined;
    const busy = captionMutation.isPending;
    const canEdit = file?.can_edit === true;

    return <Modal isOpen title={file?.original_name ?? "Файл проекта"} size="xl" layout="workspace"
        isDismissable={!busy} onOpenChange={open => { if (!open && !busy) onClose(); }}
        header={file && descriptor ? <div className="flex min-w-0 items-start gap-3">
            <FileTypeIcon file={descriptor} />
            <div className="min-w-0 flex-1">
                <h2 title={file.original_name} className="line-clamp-3 text-[15px] font-semibold leading-snug break-words text-primary [overflow-wrap:anywhere]">{file.original_name}</h2>
                <p className="mt-1 text-[12px] text-muted">{fileTypeLabel(descriptor)} · {formatFileSize(file.size)}</p>
                <p className="mt-1 text-[12px] break-words text-secondary [overflow-wrap:anywhere]">Загрузил: {file.uploaded_by_name || "Автор недоступен"}</p>
                <time dateTime={file.created_at} className="mt-1 block text-[12px] text-muted">{EXACT_DATE.format(new Date(file.created_at))}</time>
            </div>
        </div> : undefined}
        footer={<>
            {file && canEdit && <>
                <Button variant="destructive" disabled={busy || retry.isPending} icon={<Trash2 size={14} />} onClick={() => onDelete(file)}>Удалить</Button>
                <Button icon={<RotateCw size={14} />} disabled={busy || isProjectFileProcessing(file) || retry.isPending}
                    onClick={() => { setOffsets([0]); retry.mutate(file.id); }}>Повторить обработку</Button>
            </>}
            <a href={apiUrl(endpoints.projectFileContent(projectId, fileId))} download={file?.original_name}
                className="touch-target material-metal inline-flex min-h-8 items-center justify-center gap-2 rounded-[var(--radius-control)] border border-line px-3 py-1 text-[13px] font-medium text-primary hover:bg-hover focus-visible:outline-2 focus-visible:outline-accent">
                <Download size={14} aria-hidden="true" />Скачать оригинал
            </a>
        </>}
    >
        {detail.isPending && <div role="status" aria-label="Загрузка файла" className="p-5"><Skeleton className="h-52 w-full" /></div>}
        {detail.error && <div className="p-4"><ErrorMessage title="Не удалось открыть файл" message={detail.error.message} action={
            <Button disabled={detail.isFetching} onClick={() => void detail.refetch()}>Повторить загрузку файла</Button>
        } /></div>}
        {inaccessible && !detail.error && textQuery.error && <div className="p-4"><ErrorMessage message={textQuery.error.message} /></div>}
        {file && retry.variables === file.id && retry.error && <div className="px-4 pt-3"><ErrorMessage title="Не удалось повторить обработку" message={retry.error.message} /></div>}
        {file && <Tabs selectedKey={tab} onSelectionChange={key => setTab(String(key))} className="flex min-h-0 flex-1 flex-col">
            <TabList aria-label="Разделы файла" className="flex shrink-0 gap-1 border-b border-line px-3 pt-2">
                <Tab id="document" className="brand-tab flex min-h-11 cursor-pointer items-center rounded-t-[var(--radius-control)] px-3 py-2 text-[13px] text-secondary outline-none data-[selected]:bg-accent/[0.075] data-[selected]:text-primary data-[focus-visible]:ring-2 data-[focus-visible]:ring-accent">Документ</Tab>
                <Tab id="text" className="brand-tab flex min-h-11 cursor-pointer items-center rounded-t-[var(--radius-control)] px-3 py-2 text-[13px] text-secondary outline-none data-[selected]:bg-accent/[0.075] data-[selected]:text-primary data-[focus-visible]:ring-2 data-[focus-visible]:ring-accent">Текстовое представление</Tab>
            </TabList>
            <TabPanel id="document" shouldForceMount className={PANEL_CLASS}>
                <div className="flex min-w-0 flex-col gap-5">
                    <ProjectFileOriginal file={file} />
                    <section aria-label="Комментарий к файлу" className="flex min-w-0 flex-col gap-2 border-t border-line-subtle pt-4">
                        <h3 className="text-[13px] font-semibold text-primary">Комментарий к файлу</h3>
                        {editingCaption ? <>
                            <Textarea aria-label="Комментарий к файлу" autoFocus rows={4} maxLength={2000} value={captionDraft} disabled={busy}
                                onChange={event => setCaptionDraft(event.target.value)} />
                            <p className="text-[11px] text-muted">{captionDraft.length} / 2000</p>
                            {!canEdit && <p className="text-[12px] text-muted">Изменение файла сейчас недоступно.</p>}
                            {captionMutation.error && <ErrorMessage title="Не удалось сохранить комментарий" message={captionMutation.error.message} />}
                            <div className="flex flex-wrap gap-2">
                                <Button variant="primary" disabled={busy || !canEdit} onClick={async () => {
                                    try { await captionMutation.mutateAsync({ fileId, caption: captionDraft.trim() || null }); setEditingCaption(false); } catch { /* Черновик остаётся в редакторе для явного повтора. */ }
                                }}>{busy ? "Сохранение…" : "Сохранить комментарий"}</Button>
                                <Button disabled={busy} onClick={() => { setEditingCaption(false); captionMutation.reset(); }}>Отмена</Button>
                            </div>
                        </> : <>
                            {file.caption && <p className="whitespace-pre-wrap text-[13px] leading-relaxed break-words text-secondary [overflow-wrap:anywhere]">{file.caption}</p>}
                            {canEdit && <Button className="self-start" icon={<Pencil size={14} />} onClick={() => {
                                captionMutation.reset(); setCaptionDraft(file.caption ?? ""); setEditingCaption(true);
                            }}>{file.caption ? "Изменить комментарий" : "Добавить комментарий"}</Button>}
                            {!file.caption && !canEdit && <p className="text-[13px] text-muted">Комментарий не добавлен.</p>}
                        </>}
                    </section>
                </div>
            </TabPanel>
            <TabPanel id="text" shouldForceMount className={PANEL_CLASS}>
                <div className="flex min-w-0 flex-col gap-4">
                    <div>
                        <FileStatus file={file} />
                        {file.extraction_detail && <p className="mt-2 text-[13px] break-words text-secondary">{file.extraction_detail}</p>}
                        {file.fts_detail && <p className="mt-1 text-[13px] break-words text-secondary">{file.fts_detail}</p>}
                        {file.index_detail && <p className="mt-1 text-[13px] break-words text-secondary">{file.index_detail}</p>}
                    </div>
                    {file.summary && <div><h3 className="text-[13px] font-semibold text-primary">Краткое описание</h3><p className="mt-1 whitespace-pre-wrap text-[13px] break-words text-secondary [overflow-wrap:anywhere]">{file.summary}</p></div>}
                    <h3 className="text-sm font-semibold text-primary">Извлечённый текст</h3>
                    {file.text_total_chars > 0 ? <>
                        <nav aria-label="Страницы текста" className="flex flex-wrap items-center gap-2">
                            <Button icon={<ChevronLeft size={14} />} disabled={offsets.length === 1 || textQuery.isFetching} onClick={() => setOffsets(current => current.slice(0, -1))}>Назад</Button>
                            {textPage && !textQuery.error && !textQuery.isFetching && <span className="text-[12px] text-muted">{textPage.text_offset + 1}–{textPage.next_offset ?? textPage.text_total_chars} из {textPage.text_total_chars}</span>}
                            <Button icon={<ChevronRight size={14} />} disabled={!textPage || textPage.next_offset === null || textQuery.isFetching || !!textQuery.error}
                                onClick={() => { if (textPage?.next_offset != null) setOffsets(current => [...current, textPage.next_offset!]); }}>Далее</Button>
                        </nav>
                        {textQuery.isFetching && <p role="status" className="text-[13px] text-muted">Загрузка текста…</p>}
                        {textQuery.error ? <ErrorMessage message={textQuery.error.message} action={<Button onClick={() => void textQuery.refetch()}>Повторить загрузку текста</Button>} /> :
                            textPage && <pre aria-label="Извлечённый текст" tabIndex={0} className="whitespace-pre-wrap rounded-[var(--radius-control)] bg-app/50 p-3 font-sans text-[13px] leading-relaxed break-words text-primary [overflow-wrap:anywhere] focus-visible:outline-2 focus-visible:outline-accent">{textPage.text}</pre>}
                    </> : <p role="status" className="text-[13px] text-muted">{file.extraction_status === "disabled"
                        ? "Распознавание изображения отключено. Оригинал, название и комментарий доступны."
                        : ["pending", "processing"].includes(file.extraction_status) ? "Файл обрабатывается. Текст появится автоматически."
                            : file.extraction_status === "failed" ? "Не удалось извлечь текст. Оригинал доступен во вкладке «Документ». Можно повторить обработку."
                                : "Извлечённого текста нет. Оригинал доступен во вкладке «Документ» и для скачивания."}</p>}
                </div>
            </TabPanel>
        </Tabs>}
    </Modal>;
}

function FileStatus({ file }: { file: ProjectFile }) {
    return <div className="flex flex-wrap gap-x-3 gap-y-1 text-[12px]">
        <span className={cn(file.extraction_status === "failed" ? "text-danger" : "text-secondary")}>{EXTRACTION_LABELS[file.extraction_status]}</span>
        {file.fts_status && <span className={file.fts_status === "failed" ? "text-danger" : "text-muted"}>{file.fts_status === "pending" ? "Готовим для поиска" : file.fts_status === "ready" ? "В поиске" : "Поиск временно недоступен"}</span>}
        <span className={cn(file.index_status === "failed" ? "text-danger" : "text-muted")}>{INDEX_LABELS[file.index_status]}</span>
    </div>;
}
