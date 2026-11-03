import { useDeferredValue, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Plus, Search } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { DocumentDetail, DocumentListItem } from "@/lib/types";
import { formatRelative } from "@/lib/dates";
import { useProjectOutlet } from "@/lib/useProjectOutlet";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field, Input } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { SearchHighlight } from "@/components/ui/SearchHighlight";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";

export function ProjectDocumentsPage() {
    const project = useProjectOutlet();
    const queryClient = useQueryClient();
    const [search, setSearch] = useState("");
    const [isCreateOpen, setCreateOpen] = useState(false);
    const [newTitle, setNewTitle] = useState("");
    const deferredSearch = useDeferredValue(search.trim());

    const documentsQuery = useQuery({
        queryKey: queryKeys.documents(project.id, deferredSearch),
        queryFn: () =>
            api.get<DocumentListItem[]>(
                deferredSearch
                    ? `${endpoints.projectDocuments(project.id)}?search=${encodeURIComponent(deferredSearch)}`
                    : endpoints.projectDocuments(project.id),
            ),
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
        },
    });

    return (
        <div className="scrollbar-thin h-full overflow-y-auto">
            <div className="mx-auto flex w-full min-w-0 max-w-none flex-col gap-4 px-3 py-4 pb-24 sm:px-5 sm:py-5">
                <div className="material-metal -mx-2 flex flex-wrap items-center gap-2 rounded-[var(--radius-card)] border border-line-subtle px-2 py-2 shadow-card">
                    <div className="relative min-w-0 flex-1 sm:max-w-xs">
                        <Search
                            size={14}
                            aria-hidden="true"
                            className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-disabled"
                        />
                        <Input
                            value={search}
                            aria-label="Поиск документов проекта"
                            placeholder="Заголовок или содержимое"
                            className="pl-8"
                            onChange={(event) => setSearch(event.target.value)}
                        />
                    </div>
                    <Button
                        variant="primary"
                        icon={<Plus size={15} />}
                        onClick={() => setCreateOpen(true)}
                    >
                        Документ
                    </Button>
                </div>

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

                <div className="flex flex-col gap-2">
                    {documentsQuery.data?.map((document) => (
                        <Card key={document.id} interactive>
                            <Link
                                to={`/projects/${project.key}/docs/${document.slug}`}
                                className="flex min-w-0 flex-col gap-1 px-4 py-3"
                            >
                                <div className="flex min-w-0 items-center gap-2">
                                    <FileText size={14} className="shrink-0 text-muted" aria-hidden="true" />
                                    <span className="min-w-0 truncate text-[14px] font-medium text-primary">
                                        <SearchHighlight text={document.search_title ?? document.title} />
                                    </span>
                                </div>
                                {document.search_excerpt && (
                                    <p className="line-clamp-2 text-[12px] text-muted">
                                        <SearchHighlight text={document.search_excerpt} />
                                    </p>
                                )}
                                <p className="text-[11px] text-disabled">
                                    Изменён {formatRelative(document.updated_at)}
                                </p>
                            </Link>
                        </Card>
                    ))}
                </div>
            </div>

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
