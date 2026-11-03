import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Pencil, Trash2 } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { DocumentDetail, DocumentListItem, LinkedTask } from "@/lib/types";
import { formatRelative } from "@/lib/dates";
import { useProjectOutlet } from "@/lib/useProjectOutlet";
import { useRenderedMarkdown } from "@/lib/useRenderedMarkdown";
import { useUiStore } from "@/stores/ui";
import { Button, IconButton } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Input, Textarea } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";

function RenderedDocument({ markdown }: { markdown: string }) {
    const html = useRenderedMarkdown(markdown);
    return (
        <div
            className="markdown-body"
            // Содержимое очищается DOMPurify внутри renderMarkdown.
            dangerouslySetInnerHTML={{ __html: html }}
        />
    );
}

export function DocumentDetailPage() {
    const project = useProjectOutlet();
    const { slug } = useParams<{ slug: string }>();
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const setSelectedTaskId = useUiStore((state) => state.setSelectedTaskId);
    const [isEditing, setEditing] = useState(false);
    const [titleDraft, setTitleDraft] = useState("");
    const [contentDraft, setContentDraft] = useState("");
    const [isDeleteOpen, setDeleteOpen] = useState(false);

    const listQuery = useQuery({
        queryKey: queryKeys.documents(project.id),
        queryFn: () => api.get<DocumentListItem[]>(endpoints.projectDocuments(project.id)),
    });

    const documentId = listQuery.data?.find((item) => item.slug === slug)?.id;

    const documentQuery = useQuery({
        queryKey: queryKeys.document(documentId ?? 0),
        queryFn: () => api.get<DocumentDetail>(endpoints.document(documentId as number)),
        enabled: documentId !== undefined,
    });

    const linksQuery = useQuery({
        queryKey: queryKeys.documentLinks(documentId ?? 0),
        queryFn: () => api.get<LinkedTask[]>(endpoints.documentLinks(documentId as number)),
        enabled: documentId !== undefined,
    });

    function startEditing(document: DocumentDetail) {
        setTitleDraft(document.title);
        setContentDraft(document.content_md);
        setEditing(true);
    }

    const saveMutation = useMutation({
        mutationFn: () =>
            api.patch<DocumentDetail>(endpoints.document(documentId as number), {
                title: titleDraft.trim(),
                content_md: contentDraft,
            }),
        onSuccess: () => {
            setEditing(false);
            queryClient.invalidateQueries({ queryKey: ["projects", project.id, "documents"] });
            queryClient.invalidateQueries({ queryKey: queryKeys.document(documentId as number) });
        },
    });

    const deleteMutation = useMutation({
        mutationFn: () => api.delete(endpoints.document(documentId as number)),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ["projects", project.id, "documents"] });
            navigate(`/projects/${project.key}/docs`);
        },
    });

    if (listQuery.isPending || documentQuery.isPending) {
        return (
            <div className="mx-auto flex w-full max-w-none flex-col gap-3 px-5 py-5">
                <Skeleton className="h-8 w-64" />
                <Skeleton className="h-64 w-full" />
            </div>
        );
    }

    if (documentId === undefined) {
        return (
            <div className="mx-auto w-full max-w-none px-5 py-5">
                <EmptyState
                    title="Документ не найден"
                    description={`В проекте нет документа со slug «${slug}».`}
                    action={
                        <Link
                            to={`/projects/${project.key}/docs`}
                            className="text-[13px] text-accent hover:text-accent-hover"
                        >
                            Ко всем документам
                        </Link>
                    }
                />
            </div>
        );
    }

    const document = documentQuery.data;

    return (
        <div className="scrollbar-thin h-full overflow-y-auto">
            <div className="mx-auto flex w-full min-w-0 max-w-none flex-col gap-4 px-3 py-4 pb-24 sm:px-5 sm:py-5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <Link
                        to={`/projects/${project.key}/docs`}
                        className="inline-flex items-center gap-1.5 text-[13px] text-muted hover:text-secondary"
                    >
                        <ArrowLeft size={14} aria-hidden="true" />
                        Документы
                    </Link>
                    <div className="flex items-center gap-1">
                        {!isEditing && documentQuery.data && (
                            <IconButton
                                label="Редактировать документ"
                                onClick={() => startEditing(documentQuery.data)}
                            >
                                <Pencil size={14} aria-hidden="true" />
                            </IconButton>
                        )}
                        <IconButton
                            label="Удалить документ"
                            variant="destructive"
                            onClick={() => setDeleteOpen(true)}
                        >
                            <Trash2 size={14} aria-hidden="true" />
                        </IconButton>
                    </div>
                </div>

                {saveMutation.error && (
                    <ErrorMessage message={(saveMutation.error as Error).message} />
                )}

                {isEditing ? (
                    <Card className="flex flex-col gap-3 p-4">
                        <Input
                            value={titleDraft}
                            aria-label="Заголовок документа"
                            onChange={(event) => setTitleDraft(event.target.value)}
                        />
                        <Textarea
                            rows={22}
                            value={contentDraft}
                            aria-label="Содержимое документа в Markdown"
                            className="font-mono text-[12px]"
                            onChange={(event) => setContentDraft(event.target.value)}
                        />
                        <div className="flex justify-end gap-2">
                            <Button onClick={() => setEditing(false)}>Отмена</Button>
                            <Button
                                variant="primary"
                                disabled={titleDraft.trim() === "" || saveMutation.isPending}
                                onClick={() => saveMutation.mutate()}
                            >
                                Сохранить
                            </Button>
                        </div>
                    </Card>
                ) : (
                    document && (
                        <>
                            <header className="flex flex-col gap-1">
                                <h1 className="text-xl font-semibold break-words text-primary">
                                    {document.title}
                                </h1>
                                <p className="text-[11px] text-disabled">
                                    <span className="font-mono">{document.slug}</span> · изменён{" "}
                                    {formatRelative(document.updated_at)}
                                </p>
                            </header>

                            <div className="min-w-0 overflow-x-auto rounded-[var(--radius-card)] bg-surface/35 p-3 sm:p-5">
                                <RenderedDocument markdown={document.content_md} />
                            </div>
                        </>
                    )
                )}

                {(linksQuery.data?.length ?? 0) > 0 && (
                    <section className="flex flex-col gap-2">
                        <h2 className="text-[11px] font-semibold tracking-[0.06em] text-muted uppercase">
                            Связанные задачи
                        </h2>
                        <div className="rounded-[var(--radius-card)] bg-surface/45 p-1.5">
                            {linksQuery.data?.map((link) => (
                                <button
                                    key={link.link_id}
                                    type="button"
                                    onClick={() => setSelectedTaskId(link.task_id)}
                                    className="flex w-full min-w-0 items-center gap-3 rounded-md px-2.5 py-2 text-left hover:bg-hover"
                                >
                                    <span className="w-20 shrink-0 font-mono text-[11px] text-muted">
                                        {link.key}
                                    </span>
                                    <span className="min-w-0 flex-1 truncate text-[13px] text-secondary">
                                        {link.title}
                                    </span>
                                </button>
                            ))}
                        </div>
                    </section>
                )}
            </div>

            <Modal
                title="Удалить документ?"
                description="Документ и его связи с задачами будут удалены безвозвратно."
                isOpen={isDeleteOpen}
                onOpenChange={(open) => {
                    if (!open) {
                        setDeleteOpen(false);
                    }
                }}
                footer={
                    <>
                        <Button onClick={() => setDeleteOpen(false)}>Отмена</Button>
                        <Button
                            variant="destructive"
                            disabled={deleteMutation.isPending}
                            onClick={() => deleteMutation.mutate()}
                        >
                            Удалить
                        </Button>
                    </>
                }
            >
                {deleteMutation.error && (
                    <ErrorMessage message={(deleteMutation.error as Error).message} />
                )}
            </Modal>
        </div>
    );
}
