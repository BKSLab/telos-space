import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Download, ExternalLink, Pencil, RotateCw, Trash2 } from "lucide-react";
import { Tab, TabList, TabPanel, Tabs } from "react-aria-components";
import { api, apiUrl, ApiError, endpoints, queryKeys } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/dates";
import { DOCUMENT_TEXT_PAGE_SIZE, MAX_DOCUMENT_ORIGINAL_PREVIEW_SIZE, documentFragmentParts, documentsPollInterval, readDocumentOriginal } from "@/lib/documents";
import { fileExtension, fileTypeLabel, formatFileSize } from "@/lib/files";
import type { DocumentDetail, DocumentFragment, DocumentTextPage } from "@/lib/types";
import { useRefreshDocuments } from "@/lib/useDocuments";
import { useRenderedMarkdown } from "@/lib/useRenderedMarkdown";
import { useUiStore } from "@/stores/ui";
import { Button, IconButton } from "@/components/ui/Button";
import { Field, Input, Textarea } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { ErrorMessage, Skeleton } from "@/components/ui/States";
import { FileTypeIcon } from "@/components/files/FileTypeIcon";

interface DocumentViewProps {
    documentRef: string;
    fragment?: DocumentFragment | null;
    projectKey?: string;
    compact?: boolean;
    expectedProjectId?: number;
    onDeleted?: () => void;
}

const PANEL = "scrollbar-thin min-h-0 min-w-0 flex-1 overflow-y-auto p-3 outline-none data-[inert]:hidden data-[focus-visible]:outline-2 data-[focus-visible]:-outline-offset-2 data-[focus-visible]:outline-accent sm:p-4";

export function DocumentDialog({ documentRef, fragment, projectKey, expectedProjectId, isOpen = true, onClose }: Omit<DocumentViewProps, "compact" | "onDeleted"> & { isOpen?: boolean; onClose: () => void }) {
    return <Modal title="Документ" size="xl" layout="workspace" isOpen={isOpen} closeLabel="Закрыть документ" onOpenChange={open => { if (!open) onClose(); }}>
        {isOpen && <DocumentView documentRef={documentRef} fragment={fragment} projectKey={projectKey} expectedProjectId={expectedProjectId} onDeleted={onClose} />}
    </Modal>;
}

export function DocumentView(props: DocumentViewProps) {
    return <DocumentViewContent key={`${props.documentRef}:${props.fragment?.content_hash ?? ""}:${props.fragment?.start ?? ""}:${props.fragment?.end ?? ""}`} {...props} />;
}

function MarkdownBody({ text }: { text: string }) {
    const html = useRenderedMarkdown(text);
    return <div className="markdown-body min-w-0 [overflow-wrap:anywhere] [&_pre]:max-w-full [&_table]:block [&_table]:max-w-full [&_table]:overflow-x-auto" dangerouslySetInnerHTML={{ __html: html }} />;
}

function DocumentViewContent({ documentRef, fragment, projectKey, expectedProjectId, compact, onDeleted }: DocumentViewProps) {
    const refresh = useRefreshDocuments();
    const openTask = useUiStore(state => state.setSelectedTaskId);
    const [tab, setTab] = useState(fragment ? "text" : "document");
    const [position, setPosition] = useState<{ hash: string | null; offset: number } | null>(null);
    const [editing, setEditing] = useState(false);
    const [titleDraft, setTitleDraft] = useState("");
    const [captionDraft, setCaptionDraft] = useState("");
    const [contentDraft, setContentDraft] = useState("");
    const [editRevision, setEditRevision] = useState<number>();
    const [deleteOpen, setDeleteOpen] = useState(false);
    const [deleted, setDeleted] = useState(false);
    const markRef = useRef<HTMLElement>(null);
    const editButtonRef = useRef<HTMLButtonElement>(null);
    const restoreEditorFocus = useRef(false);
    const detail = useQuery({
        queryKey: queryKeys.document(documentRef),
        queryFn: () => api.get<DocumentDetail>(endpoints.document(documentRef)),
        retry: false,
        staleTime: 0,
        refetchOnMount: "always",
        refetchInterval: query => documentsPollInterval(query.state.data ? [query.state.data] : undefined, query.state.status === "error"),
    });
    const document = detail.data;
    const currentFragment = fragment?.document_ref === documentRef && fragment.content_hash === document?.text.content_hash;
    const initialOffset = currentFragment && fragment ? Math.max(0, fragment.start - Math.max(0, Math.min(500, DOCUMENT_TEXT_PAGE_SIZE - (fragment.end - fragment.start)))) : 0;
    const requestedOffset = position?.hash === document?.text.content_hash ? position?.offset ?? initialOffset : initialOffset;
    const offset = requestedOffset < (document?.text.total_chars ?? 0) ? requestedOffset : 0;
    function setOffset(offset: number) { setPosition({ hash: document?.text.content_hash ?? null, offset }); }
    function closeEditor() {
        restoreEditorFocus.current = true;
        setEditing(false);
    }
    const pageSize = DOCUMENT_TEXT_PAGE_SIZE;
    const text = useQuery({
        queryKey: [...queryKeys.documentText(documentRef, offset, pageSize), document?.text.content_hash],
        queryFn: () => api.get<DocumentTextPage>(`${endpoints.documentText(documentRef)}?${new URLSearchParams({ offset: String(offset), max_chars: String(pageSize) })}`),
        enabled: detail.isFetchedAfterMount && !detail.error && document?.text.available === true && (expectedProjectId === undefined || document.ownership.project_id === expectedProjectId),
        retry: false,
    });
    const page = text.data;
    const currentTextHash = document?.text.content_hash;
    const parts = useMemo(() => page && fragment && page.content_hash === currentTextHash ? documentFragmentParts(page, fragment) : null, [page, fragment, currentTextHash]);
    const referenceStale = !!fragment && !!document && (fragment.document_ref !== documentRef || fragment.content_hash !== document.text.content_hash || !document.text.available || (!!page && !parts));
    useEffect(() => {
        if (!parts || tab !== "text") return;
        const frame = requestAnimationFrame(() => markRef.current?.scrollIntoView({ block: "center", behavior: "auto" }));
        return () => cancelAnimationFrame(frame);
    }, [parts, tab]);

    const loadEdit = useMutation({
        mutationFn: async () => {
            let content = "";
            if (document!.source_kind === "text") {
                let next: number | null = 0;
                let hash: string | null = null;
                while (next !== null) {
                    const result: DocumentTextPage = await api.get(`${endpoints.documentText(documentRef)}?${new URLSearchParams({ offset: String(next), max_chars: String(DOCUMENT_TEXT_PAGE_SIZE) })}`);
                    if (hash !== null && result.content_hash !== hash) throw new Error("Документ изменился во время загрузки. Откройте редактор снова.");
                    hash = result.content_hash;
                    content += result.text;
                    if (result.next_offset !== null && result.next_offset <= next) throw new Error("Не удалось загрузить весь документ.");
                    next = result.next_offset;
                }
            }
            return { content, document: document! };
        },
        onSuccess: ({ content, document }) => {
            setTitleDraft(document.title); setCaptionDraft(document.caption ?? ""); setContentDraft(content);
            setEditRevision(document.revision); setEditing(true);
        },
    });
    const save = useMutation({
        mutationFn: () => api.patch<DocumentDetail>(endpoints.document(documentRef), {
            title: titleDraft.trim(), caption: captionDraft.trim() || null, expected_revision: editRevision,
            ...(document?.source_kind === "text" ? { content_md: contentDraft } : {}),
        }),
        onSuccess: () => { closeEditor(); refresh(); },
    });
    const retry = useMutation({ mutationFn: () => api.post(endpoints.documentRetry(documentRef)), onSuccess: refresh });
    const remove = useMutation({
        mutationFn: () => api.delete(endpoints.document(documentRef)),
        onSuccess: () => { setDeleteOpen(false); setDeleted(true); refresh(); onDeleted?.(); },
    });
    const busy = save.isPending || remove.isPending || loadEdit.isPending;
    useEffect(() => {
        if (editing || busy || !restoreEditorFocus.current) return;
        const button = editButtonRef.current;
        if (!button || button.disabled) return;
        button.focus();
        restoreEditorFocus.current = false;
    }, [editing, busy, detail.isSuccess]);
    if (deleted) return <p role="status" className="p-4 text-sm text-muted">Документ удалён.</p>;
    if (detail.isPending || !detail.isFetchedAfterMount) return <div role="status" aria-label="Загрузка документа" className="p-4"><Skeleton className="h-8 w-2/3" /><Skeleton className="mt-4 h-48 w-full" /></div>;
    if (detail.error) return <ErrorMessage className="m-3" title="Не удалось открыть документ" message={detail.error instanceof ApiError && [403, 404].includes(detail.error.status) ? "Объект удалён или у вас нет доступа." : detail.error.message} action={<Button onClick={() => void detail.refetch()}>Повторить загрузку документа</Button>} />;
    if (!document) return null;
    if (!document.capabilities.can_read) return <ErrorMessage className="m-3" title="Не удалось открыть документ" message="Объект удалён или у вас нет доступа." />;
    if (expectedProjectId !== undefined && document.ownership.project_id !== expectedProjectId) return <ErrorMessage className="m-3" title="Документ недоступен в этом проекте" message="Документ перемещён или его проектный контекст изменился." />;
    const file = { name: document.original?.filename ?? "document.md", mime: document.original?.content_type ?? "text/markdown" };
    return <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="flex shrink-0 flex-col gap-3 border-b border-line-subtle px-3 py-3 sm:px-4">
            <div className="flex min-w-0 items-start gap-3">
                <FileTypeIcon file={file} className="h-9 w-9" />
                <div className="min-w-0 flex-1">
                    <h2 className="break-words text-[15px] font-semibold text-primary [overflow-wrap:anywhere]">{document.title}</h2>
                    <p className="mt-1 text-[11px] break-words text-muted [overflow-wrap:anywhere]">{fileTypeLabel(file)}{document.original ? ` · ${formatFileSize(document.original.size)}` : ""} · {document.author?.name ?? "Без автора"}</p>
                </div>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
                {document.original && <a href={apiUrl(document.original.content_url)} download className="touch-target inline-flex items-center gap-1.5 rounded px-2 py-1.5 text-[12px] text-accent hover:bg-hover focus-visible:outline-2 focus-visible:outline-accent"><Download size={13} />Скачать оригинал</a>}
                {compact && projectKey && <Link to={`/projects/${projectKey}/docs/${encodeURIComponent(document.ref)}`} className="touch-target inline-flex items-center gap-1.5 rounded px-2 py-1.5 text-[12px] text-accent hover:bg-hover"><ExternalLink size={13} />Открыть полностью</Link>}
                {document.capabilities.can_edit && !editing && <Button ref={editButtonRef} size="sm" disabled={busy} icon={<Pencil size={13} />} onClick={() => { save.reset(); loadEdit.mutate(); }}>{loadEdit.isPending ? "Загрузка…" : "Редактировать"}</Button>}
                {document.capabilities.can_retry && <Button size="sm" disabled={retry.isPending || busy} icon={<RotateCw size={13} />} onClick={() => retry.mutate()}>Повторить обработку</Button>}
                {document.capabilities.can_delete && <IconButton label="Удалить документ" variant="destructive" disabled={busy} onClick={() => setDeleteOpen(true)}><Trash2 size={14} /></IconButton>}
            </div>
            {loadEdit.error && <ErrorMessage title="Не удалось открыть редактор" message={loadEdit.error.message} />}
            {retry.error && <ErrorMessage title="Не удалось повторить обработку" message={retry.error.message} />}
        </header>
        {editing ? <div className="scrollbar-thin flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3 sm:p-4">
            <Field label="Заголовок документа">{id => <Input id={id} autoFocus maxLength={255} value={titleDraft} disabled={save.isPending} onChange={event => setTitleDraft(event.target.value)} />}</Field>
            <Field label="Подпись к документу">{id => <Textarea id={id} rows={2} maxLength={2000} value={captionDraft} disabled={save.isPending} onChange={event => setCaptionDraft(event.target.value)} />}</Field>
            {document.source_kind === "text" && <Field label="Содержимое документа в Markdown">{id => <Textarea id={id} rows={20} className="font-mono text-[12px]" value={contentDraft} disabled={save.isPending} onChange={event => setContentDraft(event.target.value)} />}</Field>}
            {save.error && <ErrorMessage title="Документ не сохранён" message={`${save.error.message} Черновик сохранён.`} />}
            <div className="flex flex-wrap justify-end gap-2"><Button disabled={save.isPending} onClick={closeEditor}>Отмена</Button><Button variant="primary" disabled={!titleDraft.trim() || save.isPending} onClick={() => save.mutate()}>Сохранить документ</Button></div>
        </div> : <Tabs selectedKey={tab} onSelectionChange={key => setTab(String(key))} className="flex min-h-0 min-w-0 flex-1 flex-col">
            <TabList aria-label="Представление документа" className="flex shrink-0 overflow-x-auto border-b border-line-subtle px-3">
                {[['document', 'Документ'], ['text', 'Текст'], ['details', 'Сведения']].map(([id, label]) => <Tab key={id} id={id} className="brand-tab touch-target cursor-pointer px-3 py-2 text-[12px] text-muted outline-none data-[selected]:text-primary data-[focus-visible]:outline-2 data-[focus-visible]:outline-accent">{label}</Tab>)}
            </TabList>
            <TabPanel id="document" className={PANEL}>
                {document.caption && <p className="mb-4 whitespace-pre-wrap break-words text-[13px] text-secondary [overflow-wrap:anywhere]">{document.caption}</p>}
                {document.source_kind === "text" ? <>
                    {text.isPending && document.text.available && <Skeleton className="h-40 w-full" />}
                    {!document.text.available && <p className="text-[13px] text-muted">Документ пока пуст. Добавьте текст через «Редактировать».</p>}
                    {text.error && <ErrorMessage title="Не удалось загрузить текст" message={text.error.message} action={<Button onClick={() => void text.refetch()}>Повторить загрузку текста</Button>} />}
                    {page && <><TextPagination page={page} busy={text.isFetching} onOffsetChange={setOffset} /><MarkdownBody text={page.text} /></>}
                </> : <DocumentOriginal document={document} />}
            </TabPanel>
            <TabPanel id="text" className={PANEL}>
                <div className="flex min-w-0 flex-col gap-3">
                    {fragment && <p role="status" className={cn("rounded border px-3 py-2 text-[12px]", referenceStale ? "border-warning/30 bg-warning/5 text-warning" : "border-line-subtle text-muted")}>
                        {referenceStale ? "Документ изменился или фрагмент недоступен. Открыт документ без подсветки." : parts ? "Показан фрагмент текущей версии документа." : "Проверяем фрагмент документа…"}
                    </p>}
                    {text.isPending && document.text.available && <Skeleton className="h-40 w-full" />}
                    {text.error && <ErrorMessage title="Не удалось загрузить текст" message={text.error.message} action={<Button onClick={() => void text.refetch()}>Повторить загрузку текста</Button>} />}
                    {!document.text.available && <p role="status" className="text-[13px] text-muted">{["pending", "processing"].includes(document.states.extraction.status) ? "Документ обрабатывается. Текст появится автоматически." : "Текст недоступен. Оригинал можно скачать; название и подпись доступны для поиска."}</p>}
                    {page && <>
                        <TextPagination page={page} busy={text.isFetching} onOffsetChange={setOffset} />
                        <pre tabIndex={0} aria-label={page.rendition === "markdown" ? "Исходный текст Markdown" : "Извлечённый текст"} className="min-w-0 whitespace-pre-wrap rounded-[var(--radius-control)] bg-app/40 p-3 font-sans text-[13px] leading-relaxed break-words text-primary [overflow-wrap:anywhere] focus-visible:outline-2 focus-visible:outline-accent">{parts ? <>{parts.before}<mark ref={markRef} className="rounded-sm bg-warning/25 text-primary outline outline-1 outline-warning/50">{parts.match}</mark>{parts.after}</> : page.text}</pre>
                    </>}
                </div>
            </TabPanel>
            <TabPanel id="details" className={PANEL}>
                <dl className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-3 text-[12px]">
                    <dt className="text-muted">Создан</dt><dd className="break-words text-secondary">{formatDateTime(document.created_at)}</dd>
                    <dt className="text-muted">Изменён</dt><dd className="break-words text-secondary">{formatDateTime(document.updated_at)}</dd>
                    <dt className="text-muted">Автор</dt><dd className="break-words text-secondary">{document.author?.name ?? "Не указан"}</dd>
                    {Object.entries(document.states).map(([key, state]) => <StateDetail key={key} name={key === "extraction" ? "Текст" : key === "fts" ? "Поиск" : "Знания Ариса"} status={state.status} detail={state.detail} />)}
                </dl>
                {document.tasks.length > 0 && <section className="mt-5 flex min-w-0 flex-col gap-2" aria-label="Задачи документа"><h3 className="text-[12px] font-medium text-secondary">Задачи</h3>{document.tasks.map(task => <button type="button" key={task.id} onClick={() => openTask(task.id)} className="touch-target min-w-0 rounded px-2 py-2 text-left text-[12px] text-accent hover:bg-hover focus-visible:outline-2 focus-visible:outline-accent"><span className="font-mono">{task.key}</span> · {task.title}{task.relation === "owner" && <span className="text-muted"> · документ задачи</span>}</button>)}</section>}
            </TabPanel>
        </Tabs>}
        <Modal title="Удалить документ?" description="Документ, его оригинал и связи с задачами будут удалены безвозвратно." isOpen={deleteOpen} isDismissable={!remove.isPending} onOpenChange={open => { if (!open && !remove.isPending) setDeleteOpen(false); }} footer={<><Button disabled={remove.isPending} onClick={() => setDeleteOpen(false)}>Отмена</Button><Button variant="destructive" disabled={remove.isPending} onClick={() => remove.mutate()}>Удалить документ</Button></>}>
            {remove.error ? <ErrorMessage title="Не удалось удалить документ" message={remove.error.message} /> : <p className="break-words text-[13px] text-secondary">{document.title}</p>}
        </Modal>
    </div>;
}

function TextPagination({ page, busy, onOffsetChange }: { page: DocumentTextPage; busy: boolean; onOffsetChange: (offset: number) => void }) {
    if (page.offset === 0 && page.next_offset === null) return null;
    return <nav aria-label="Страницы текста документа" className="mb-3 flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={page.offset === 0 || busy} onClick={() => onOffsetChange(Math.max(0, page.offset - DOCUMENT_TEXT_PAGE_SIZE))}>Назад</Button>
        <span className="text-[11px] text-muted">{page.offset + 1}–{page.offset + Array.from(page.text).length} из {page.total_chars}</span>
        <Button size="sm" disabled={page.next_offset === null || busy} onClick={() => { if (page.next_offset !== null) onOffsetChange(page.next_offset); }}>Далее</Button>
    </nav>;
}

const STATE_LABELS: Record<string, string> = { ready: "Готово", pending: "В очереди", processing: "Обрабатывается", failed: "Ошибка", disabled: "Отключено", unsupported: "Недоступно для формата", empty: "Текст не найден", not_required: "Не требуется" };
function StateDetail({ name, status, detail }: { name: string; status: string; detail: string | null }) {
    return <><dt className="text-muted">{name}</dt><dd className="break-words text-secondary">{STATE_LABELS[status] ?? "Состояние уточняется"}{detail && <p className="mt-1 text-muted">{detail}</p>}</dd></>;
}

function DocumentOriginal({ document }: { document: DocumentDetail }) {
    const [failed, setFailed] = useState(false);
    const original = document.original;
    const extension = original ? fileExtension(original.filename) : "";
    const textOriginal = !!original && ["md", "txt", "csv", "log"].includes(extension);
    const text = useQuery({
        queryKey: ["documents", "original", document.ref],
        queryFn: ({ signal }) => readDocumentOriginal(original!.content_url, signal),
        enabled: textOriginal && original!.size <= MAX_DOCUMENT_ORIGINAL_PREVIEW_SIZE,
        retry: false, staleTime: 0, gcTime: 0,
    });
    if (!original) return null;
    if (original.preview_url && !failed) return <img src={apiUrl(original.preview_url)} alt={document.title} className="mx-auto max-h-[65dvh] max-w-full rounded object-contain" onError={() => setFailed(true)} />;
    if (textOriginal && original.size <= MAX_DOCUMENT_ORIGINAL_PREVIEW_SIZE) return <div className="min-w-0">
        {text.isPending && <Skeleton className="h-32 w-full" />}
        {text.error && <ErrorMessage title="Не удалось открыть оригинал" message={text.error.message} action={<Button onClick={() => void text.refetch()}>Повторить загрузку оригинала</Button>} />}
        {text.data !== undefined && (extension === "md" ? <MarkdownBody text={text.data} /> : <pre tabIndex={0} aria-label="Текст оригинала" className="whitespace-pre-wrap font-sans text-[13px] leading-relaxed break-words text-primary [overflow-wrap:anywhere] focus-visible:outline-2 focus-visible:outline-accent">{text.data}</pre>)}
    </div>;
    return <p className="rounded bg-app/40 p-4 text-[13px] text-muted">{failed ? "Не удалось загрузить изображение. " : ""}Оригинал доступен для скачивания. Прочитайте извлечённый текст во вкладке «Текст».</p>;
}
