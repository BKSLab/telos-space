import { useDeferredValue, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Tab, TabList, TabPanel, Tabs } from "react-aria-components";
import { Plus } from "lucide-react";
import { documentProcessingLabel, DOCUMENT_PAGE_SIZE, DOCUMENT_UPLOAD_ACCEPT, MAX_DOCUMENT_BATCH, MAX_DOCUMENT_SIZE, type MarkdownDocumentDraft } from "@/lib/documents";
import { fileDraftKey, mergeFileDrafts, type FileUploadDraft } from "@/lib/files";
import type { DocumentListItem } from "@/lib/types";
import { useDocuments } from "@/lib/useDocuments";
import { newUuid } from "@/lib/uuid";
import { FileDraftList } from "@/components/files/FileDraftList";
import { FileUploadControl } from "@/components/files/FileUploadControl";
import { FileTypeIcon } from "@/components/files/FileTypeIcon";
import { Button } from "@/components/ui/Button";
import { Field, Input, Textarea } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";
import { DocumentPagination } from "./DocumentPagination";

export interface DocumentComposeDialogProps {
    projectId?: number;
    selectedRefs?: string[];
    isOpen: boolean;
    onClose: () => void;
    onMarkdown: (draft: MarkdownDocumentDraft, requestId: string) => Promise<void> | void;
    onUpload: (draft: FileUploadDraft, requestId: string) => Promise<void> | void;
    onExisting?: (documents: DocumentListItem[]) => Promise<void> | void;
    draftMode?: boolean;
}

export function DocumentComposeDialog(props: DocumentComposeDialogProps) {
    return props.isOpen ? <DocumentComposeContent {...props} /> : null;
}

function DocumentComposeContent({ projectId, selectedRefs = [], onClose, onMarkdown, onUpload, onExisting, draftMode }: DocumentComposeDialogProps) {
    const [mode, setMode] = useState("markdown");
    const [title, setTitle] = useState("");
    const [content, setContent] = useState("");
    const [files, setFiles] = useState<FileUploadDraft[]>([]);
    const [error, setError] = useState<string | null>(null);
    const [search, setSearch] = useState("");
    const [offset, setOffset] = useState(0);
    const [selected, setSelected] = useState<Map<string, DocumentListItem>>(new Map());
    const requestIds = useRef(new Map<string, string>());
    const q = useDeferredValue(search.trim());
    const existing = useDocuments({ project_id: projectId, q, limit: DOCUMENT_PAGE_SIZE, offset }, mode === "existing" && projectId !== undefined);
    function requestId(key: string) {
        if (!requestIds.current.has(key)) requestIds.current.set(key, newUuid());
        return requestIds.current.get(key)!;
    }
    const submit = useMutation({
        mutationFn: async () => {
            setError(null);
            if (mode === "markdown") {
                const key = JSON.stringify([title, content]);
                const id = requestId(key);
                await onMarkdown({ key: id, title: title.trim(), content_md: content }, id);
                return true;
            }
            if (mode === "existing") { await onExisting?.([...selected.values()]); return true; }
            const failed: FileUploadDraft[] = [];
            const errors: string[] = [];
            for (const draft of files) {
                try { await onUpload(draft, requestId(`${fileDraftKey(draft.file)}:${draft.caption}`)); }
                catch (cause) { failed.push(draft); errors.push(`«${draft.file.name}»: ${(cause as Error).message}`); }
            }
            setFiles(failed);
            if (failed.length) { setError(errors.join(" ")); return false; }
            return true;
        },
        onSuccess: done => { if (done) onClose(); },
    });
    const busy = submit.isPending;
    const modes = [["markdown", "Создать Markdown"], ["upload", "Загрузить"], ...(projectId !== undefined && onExisting ? [["existing", "Из проекта"]] : [])];
    return <Modal title="Добавить документ" description={draftMode ? "Документы будут добавлены при создании задачи" : undefined} size="lg" tall isOpen isDismissable={!busy} onOpenChange={open => { if (!open && !busy) onClose(); }}
        footer={<><Button disabled={busy} onClick={onClose}>Отмена</Button><Button variant="primary" disabled={busy || (mode === "markdown" ? !title.trim() : mode === "upload" ? files.length === 0 : selected.size === 0)} onClick={() => submit.mutate()}>{busy ? "Добавление…" : draftMode ? "Добавить в задачу" : "Добавить"}</Button></>}>
        <div className="flex min-w-0 flex-col gap-3">
            <Tabs selectedKey={mode} onSelectionChange={key => { setMode(String(key)); submit.reset(); setError(null); }} className="min-w-0">
                <TabList aria-label="Способ добавления документа" className="mb-4 flex overflow-x-auto border-b border-line-subtle">{modes.map(([id, label]) => <Tab key={id} id={id} isDisabled={busy} className="brand-tab touch-target shrink-0 cursor-pointer px-3 py-2 text-[12px] text-muted outline-none data-[selected]:text-primary data-[focus-visible]:outline-2 data-[focus-visible]:outline-accent">{label}</Tab>)}</TabList>
                <TabPanel id="markdown" className="flex min-w-0 flex-col gap-3 outline-none">
                    <Field label="Название документа">{id => <Input id={id} autoFocus maxLength={255} value={title} disabled={busy} onChange={event => setTitle(event.target.value)} placeholder="Требования к результату" />}</Field>
                    <Field label="Текст документа · Markdown">{id => <Textarea id={id} rows={12} value={content} disabled={busy} className="font-mono text-[12px]" onChange={event => setContent(event.target.value)} placeholder="Опишите требования, решения или план" />}</Field>
                </TabPanel>
                <TabPanel id="upload" className="flex min-w-0 flex-col gap-3 outline-none">
                    <p className="text-[12px] text-muted">До 10 документов, по 10 МБ. Оригинал и извлечённый текст сохраняются в одном документе.</p>
                    <FileUploadControl label="Выбрать документы" accept={DOCUMENT_UPLOAD_ACCEPT} maxFiles={MAX_DOCUMENT_BATCH} maxFileSize={MAX_DOCUMENT_SIZE} currentCount={files.length} disabled={busy} onError={setError} onFiles={incoming => { setFiles(current => mergeFileDrafts(current, incoming)); submit.reset(); }} />
                    <FileDraftList drafts={files} disabled={busy} onChange={setFiles} />
                </TabPanel>
                {projectId !== undefined && onExisting && <TabPanel id="existing" className="flex min-w-0 flex-col gap-3 outline-none">
                    <Input aria-label="Поиск документов для связи" value={search} disabled={busy} placeholder="Название или содержимое" onChange={event => { setSearch(event.target.value); setOffset(0); }} />
                    {existing.isPending && <Skeleton className="h-28 w-full" />}
                    {existing.error && <ErrorMessage title="Не удалось загрузить документы проекта" message={existing.error.message} action={<Button onClick={() => void existing.refetch()}>Повторить</Button>} />}
                    {existing.data && <>
                        <div className="scrollbar-thin flex max-h-72 min-w-0 flex-col gap-1 overflow-y-auto">
                            {existing.data.items.map(document => <label key={document.ref} className="flex min-w-0 cursor-pointer items-center gap-2 rounded px-2 py-2 hover:bg-hover">
                                <input type="checkbox" checked={selected.has(document.ref) || selectedRefs.includes(document.ref)} disabled={busy || !document.capabilities.can_link || selectedRefs.includes(document.ref)} className="size-4 shrink-0 accent-accent" onChange={event => setSelected(current => { const next = new Map(current); if (event.target.checked) next.set(document.ref, document); else next.delete(document.ref); return next; })} />
                                <FileTypeIcon file={{ name: document.original?.filename ?? "document.md", mime: document.original?.content_type ?? "text/markdown" }} className="h-8 w-8" />
                                <span className="min-w-0 flex-1"><span className="block break-words text-[13px] text-secondary">{document.title}</span><span className="text-[10px] text-muted">{selectedRefs.includes(document.ref) ? "Уже в задаче" : !document.capabilities.can_link ? "Связь с другой задачей недоступна" : documentProcessingLabel(document)}</span></span>
                            </label>)}
                        </div>
                        {existing.data.total === 0 && <EmptyState title="Документов пока нет" description="Создайте Markdown или загрузите документ." />}
                        {selected.size > 0 && <p role="status" className="text-[12px] text-muted">Выбрано: {selected.size}</p>}
                        <DocumentPagination page={existing.data} busy={existing.isFetching || busy} onOffsetChange={setOffset} />
                    </>}
                </TabPanel>}
            </Tabs>
            {(error || submit.error) && <ErrorMessage title="Не удалось добавить документы" message={error ?? submit.error!.message} />}
        </div>
    </Modal>;
}

export function DocumentAddButton({ onClick, disabled }: { onClick: () => void; disabled?: boolean }) {
    return <Button variant="primary" icon={<Plus size={14} />} onClick={onClick} disabled={disabled}>Добавить документ</Button>;
}
