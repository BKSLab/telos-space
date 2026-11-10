import { useDeferredValue, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Plus, Search } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { DocumentDetail, DocumentListItem, FileSearchPage, FileSearchScope, Task } from "@/lib/types";
import { mergeFileDrafts, type FileUploadDraft } from "@/lib/files";
import { formatFullDate } from "@/lib/dates";
import { useProjectOutlet } from "@/lib/useProjectOutlet";
import { useProjectFiles } from "@/lib/useProjectFiles";
import { MAX_PROJECT_FILE_BATCH, MAX_PROJECT_FILE_SIZE, PROJECT_FILE_ACCEPT, projectFileId } from "@/lib/projectFiles";
import { FileUploadControl } from "@/components/files/FileUploadControl";
import { ProjectFilesSection } from "@/components/files/ProjectFilesSection";
import { FileUploadDialog } from "@/components/files/FileUploadDialog";
import { FileSearchResults } from "@/components/files/FileSearchResults";
import { FileTypeIcon } from "@/components/files/FileTypeIcon";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { SearchHighlight } from "@/components/ui/SearchHighlight";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";

export function ProjectDocumentsPage() {
    const project = useProjectOutlet();
    const queryClient = useQueryClient();
    const [search, setSearch] = useState("");
    const [isCreateOpen, setCreateOpen] = useState(false);
    const [newTitle, setNewTitle] = useState("");
    const [uploadError, setUploadError] = useState<string | null>(null);
    const [uploadDrafts, setUploadDrafts] = useState<FileUploadDraft[]>([]);
    const [scope, setScope] = useState<FileSearchScope>("all");
    const [taskFilter, setTaskFilter] = useState("");
    const [offset, setOffset] = useState(0);
    const [searchParams, setSearchParams] = useSearchParams();
    const selectedFileId = projectFileId(searchParams.get("file"));
    const deferredSearch = useDeferredValue(search.trim());
    const projectFiles = useProjectFiles(project.id, "");
    const searchActive = deferredSearch.length > 0;
    const hasSearchFilters = scope !== "all" || taskFilter !== "";
    const searchQuery = useQuery({
        queryKey: queryKeys.projectFilesSearch(project.id, deferredSearch, scope, taskFilter, offset),
        queryFn: () => {
            const params = new URLSearchParams({ q: deferredSearch, scope, limit: "20", offset: String(offset) });
            if (taskFilter) params.set("task_id", taskFilter);
            return api.get<FileSearchPage>(`${endpoints.projectFilesSearch(project.id)}?${params}`);
        },
        enabled: searchActive,
        retry: false,
    });
    const tasksQuery = useQuery({ queryKey: queryKeys.tasks(project.id), queryFn: () => api.get<Task[]>(endpoints.projectTasks(project.id)) });

    function selectFile(id: number | null) {
        setSearchParams((previous) => {
            const next = new URLSearchParams(previous);
            if (id === null) next.delete("file");
            else next.set("file", String(id));
            return next;
        }, { replace: id === null });
    }

    const documentsQuery = useQuery({
        queryKey: queryKeys.documents(project.id),
        queryFn: () => api.get<DocumentListItem[]>(endpoints.projectDocuments(project.id)),
    });

    const createMutation = useMutation({
        mutationFn: () =>
            api.post<DocumentDetail>(endpoints.projectDocuments(project.id), {
                title: newTitle.trim(),
                content_md: `# ${newTitle.trim()}\n\n`,
            }),
        onSuccess: () => {
            setNewTitle("");
            setCreateOpen(false);
            queryClient.invalidateQueries({ queryKey: ["projects", project.id, "documents"] });
            queryClient.invalidateQueries({ queryKey: queryKeys.projectFiles(project.id) });
        },
    });

    return (
        <div className="scrollbar-thin h-full overflow-y-auto">
            <div className="mx-auto flex w-full min-w-0 max-w-none flex-col gap-4 px-3 py-4 pb-24 sm:px-5 sm:py-5">
                <div className="material-metal -mx-2 flex flex-wrap items-center gap-2 rounded-[var(--radius-card)] border border-line-subtle px-2 py-2 shadow-card">
                    <div className="relative min-w-0 basis-full sm:max-w-xs sm:flex-1 sm:basis-auto">
                        <Search
                            size={14}
                            aria-hidden="true"
                            className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-disabled"
                        />
                        <Input
                            value={search}
                            aria-label="Поиск документов и файлов проекта"
                            placeholder="Название или содержимое"
                            className="pl-8"
                            onChange={(event) => { setSearch(event.target.value); setOffset(0); }}
                        />
                    </div>
                    <Button
                        variant="primary"
                        icon={<Plus size={15} />}
                        onClick={() => setCreateOpen(true)}
                    >
                        Документ
                    </Button>
                    <FileUploadControl
                        label="Загрузить файл"
                        accept={PROJECT_FILE_ACCEPT}
                        maxFiles={MAX_PROJECT_FILE_BATCH}
                        maxFileSize={MAX_PROJECT_FILE_SIZE}
                        currentCount={uploadDrafts.length}
                        uploading={projectFiles.upload.isPending}
                        onError={setUploadError}
                        onFiles={(files) => { projectFiles.upload.reset(); setUploadDrafts(current => mergeFileDrafts(current, files)); }}
                    />
                </div>
                <div className="grid min-w-0 gap-2 sm:grid-cols-2 lg:max-w-2xl">
                    <Select aria-label="Источники поиска" value={scope} onChange={event => { const value = event.target.value as FileSearchScope; setScope(value); if (value === "project_files") setTaskFilter(""); setOffset(0); }}>
                        <option value="all">Все источники</option><option value="project_files">Файлы проекта</option><option value="task_files">Файлы задач</option><option value="documents">Документы</option>
                    </Select>
                    <Select aria-label="Задача — источник файла" value={taskFilter} disabled={scope === "project_files" || tasksQuery.isPending}
                        onChange={event => { setTaskFilter(event.target.value); setOffset(0); }}>
                        <option value="">Все задачи</option>{tasksQuery.data?.map(task => <option key={task.id} value={task.id}>{task.key} · {task.title}</option>)}
                    </Select>
                </div>
                {!searchActive && hasSearchFilters && <p role="status" className="text-[12px] text-muted">Введите текст для поиска с выбранными фильтрами</p>}

                {uploadError && <ErrorMessage title="Не удалось выбрать файлы" message={uploadError} />}
                {searchActive && <section aria-label="Поиск по всем материалам" className="flex min-w-0 flex-col gap-3">
                    <h2 className="text-sm font-semibold text-primary">Результаты поиска</h2>
                    {searchQuery.isPending && <Skeleton className="h-24 w-full" />}
                    {searchQuery.error && <ErrorMessage title="Не удалось найти материалы" message={searchQuery.error.message} action={<Button onClick={() => void searchQuery.refetch()}>Повторить поиск</Button>} />}
                    {searchQuery.data && <>
                        <p role="status" className="text-[12px] text-muted">Найдено: {searchQuery.data.total}</p>
                        {searchQuery.data.items.length === 0 ? <EmptyState title="Материалы не найдены" description="Измените запрос или фильтры." /> :
                            <FileSearchResults items={searchQuery.data.items} projectKey={project.key} onOpenProjectFile={selectFile} />}
                        {searchQuery.data.total > 20 && <nav aria-label="Страницы результатов" className="flex flex-wrap items-center gap-2">
                            <Button disabled={offset === 0 || searchQuery.isFetching} onClick={() => setOffset(value => Math.max(0, value - 20))}>Назад</Button>
                            <span className="text-[12px] text-muted">{offset + 1}–{Math.min(offset + 20, searchQuery.data.total)} из {searchQuery.data.total}</span>
                            <Button disabled={offset + 20 >= searchQuery.data.total || searchQuery.isFetching} onClick={() => setOffset(value => value + 20)}>Далее</Button>
                        </nav>}
                    </>}
                </section>}

                <ProjectFilesSection
                    projectId={project.id}
                    model={projectFiles}
                    search=""
                    selectedFileId={selectedFileId}
                    onOpenFile={(id) => selectFile(id)}
                    onCloseFile={() => selectFile(null)}
                    hidden={searchActive}
                />

                {!searchActive && <>
                <h2 className="text-sm font-semibold text-primary">Документы</h2>

                {documentsQuery.error && (
                    <ErrorMessage message={(documentsQuery.error as Error).message} />
                )}

                {documentsQuery.isPending && (
                    <div className="flex flex-col gap-2">
                        {[0, 1, 2].map((index) => (
                            <Skeleton key={index} className="h-14 w-full" />
                        ))}
                    </div>
                )}

                {documentsQuery.data?.length === 0 && (
                    <EmptyState
                        title={deferredSearch ? "Ничего не найдено" : "Документов пока нет"}
                        description={
                            deferredSearch
                                ? "Измените поисковый запрос."
                                : "Соберите в проекте план, требования и решения."
                        }
                        icon={<FileText size={24} />}
                        action={
                            deferredSearch ? undefined : (
                                <Button
                                    variant="primary"
                                    icon={<Plus size={15} />}
                                    onClick={() => setCreateOpen(true)}
                                >
                                    Создать документ
                                </Button>
                            )
                        }
                    />
                )}

                <div className="min-w-0 divide-y divide-line-subtle overflow-hidden rounded-[var(--radius-card)] border border-line-subtle bg-surface/30">
                    {documentsQuery.data?.map((document) => (
                            <Link
                                key={document.id}
                                to={`/projects/${project.key}/docs/${document.slug}`}
                                className="flex min-h-14 min-w-0 items-center gap-3 px-3 py-2 hover:bg-hover focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent"
                            >
                                <FileTypeIcon file={{ name: "document.md", mime: "text/markdown" }} className="h-9 w-9" />
                                <span className="flex min-w-0 flex-1 flex-col gap-1 sm:flex-row sm:items-center sm:gap-4">
                                    <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-primary">
                                        <SearchHighlight text={document.search_title ?? document.title} />
                                    </span>
                                    <span className="flex shrink-0 items-center gap-3 text-[11px] text-muted"><span className="w-10">MD</span><span className="sm:w-36">{formatFullDate(document.updated_at)}</span></span>
                                </span>
                            </Link>
                    ))}
                </div>
                </>}
            </div>

            <FileUploadDialog drafts={uploadDrafts} onChange={setUploadDrafts} busy={projectFiles.upload.isPending}
                error={projectFiles.upload.data?.errors.join(" ")} onClose={() => setUploadDrafts([])}
                onUpload={async () => { const result = await projectFiles.upload.mutateAsync(uploadDrafts); setUploadDrafts(result.failed); }} />

            <Modal
                title="Новый документ"
                isOpen={isCreateOpen}
                onOpenChange={(open) => {
                    if (!open) {
                        setCreateOpen(false);
                    }
                }}
                footer={
                    <>
                        <Button onClick={() => setCreateOpen(false)}>Отмена</Button>
                        <Button
                            variant="primary"
                            disabled={newTitle.trim() === "" || createMutation.isPending}
                            onClick={() => createMutation.mutate()}
                        >
                            Создать
                        </Button>
                    </>
                }
            >
                <div className="flex flex-col gap-3">
                    {createMutation.error && (
                        <ErrorMessage message={(createMutation.error as Error).message} />
                    )}
                    <Field label="Заголовок">
                        {(id) => (
                            <Input
                                id={id}
                                autoFocus
                                value={newTitle}
                                placeholder="План проекта"
                                onChange={(event) => setNewTitle(event.target.value)}
                            />
                        )}
                    </Field>
                </div>
            </Modal>
        </div>
    );
}
