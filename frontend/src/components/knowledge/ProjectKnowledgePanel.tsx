import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Database, RefreshCw } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { KnowledgeEntityType } from "@/lib/types";
import { useProjectKnowledgeStatus } from "@/lib/useProjectKnowledgeStatus";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";

const SOURCE_LABELS: Record<KnowledgeEntityType, string> = {
    project: "Паспорт", task: "Задачи и чек-листы", task_report: "Отчёты задач", document: "Текстовые документы", comment: "Комментарии",
    attachment: "Загруженные документы задач", project_file: "Загруженные документы проекта", milestone: "Вехи", risk: "Риски", wbs_node: "Разделы ИСР",
    stage: "Стадии", sticker: "Стикеры", member: "Команда", activity: "История задач",
    deadline_change: "История сроков", analytics_report: "Сохранённые отчёты",
};

/** Состояние, покрытие и переиндексация знаний проекта. */
export function ProjectKnowledgePanel({ projectId }: { projectId: number }) {
    const queryClient = useQueryClient();
    const statusQuery = useProjectKnowledgeStatus(projectId);
    const status = statusQuery.data;
    const indexing = Boolean(status && (status.pending_jobs > 0 || status.processing_jobs > 0));
    const reindexMutation = useMutation({
        mutationFn: () =>
            api.post<{ queued: boolean }>(endpoints.projectKnowledgeReindex(projectId)),
        onSuccess: () => {
            void queryClient.invalidateQueries({
                queryKey: queryKeys.projectKnowledgeStatus(projectId),
            });
        },
    });

    return <>
        <section className="flex flex-col gap-3 rounded-[var(--radius-card)] bg-surface/55 p-4">
            <div className="flex items-center gap-2 text-[13px] font-semibold text-secondary">
                <Database size={14} aria-hidden="true" />
            Знания проекта
            </div>
            {statusQuery.error ? (
                <p className="text-[12px] text-danger">
                    {(statusQuery.error as Error).message}
                </p>
            ) : (
                <dl className="flex flex-col gap-2 text-[12px]">
                    <div className="flex justify-between gap-3">
                        <dt className="text-muted">Фрагментов</dt>
                        <dd className="font-mono text-secondary">
                            {status?.points_count ?? "—"}
                        </dd>
                    </div>
                    <div className="flex justify-between gap-3">
                        <dt className="text-muted">В очереди</dt>
                        <dd className="font-mono text-secondary">
                            {(status?.pending_jobs ?? 0) + (status?.processing_jobs ?? 0)}
                        </dd>
                    </div>
                    <div className="flex justify-between gap-3">
                        <dt className="text-muted">Ошибок</dt>
                        <dd className={cn("font-mono", status?.failed_jobs ? "text-danger" : "text-secondary")}>
                            {status?.failed_jobs ?? 0}
                        </dd>
                    </div>
                </dl>
            )}
            {status?.last_error && (
                <p className="line-clamp-4 rounded-md bg-danger/8 p-2 text-[11px] text-danger">
                    {status.last_error}
                </p>
            )}
            <Button
                size="sm"
                onClick={() => reindexMutation.mutate()}
                disabled={reindexMutation.isPending || indexing}
                icon={
                    <RefreshCw
                        size={13}
                        className={reindexMutation.isPending ? "animate-spin" : undefined}
                    />
                }
            >
                Переиндексировать
            </Button>
        </section>
        {Boolean(status?.coverage?.length) && (
            <section className="rounded-[var(--radius-card)] bg-surface/55 p-4 text-[12px]">
                <p className="mb-2 font-medium text-secondary">Доступно для поиска</p>
                <dl className="flex flex-col gap-1.5">
                    {status?.coverage.map((row) => (
                        <div key={row.entity_type} className="flex justify-between gap-2">
                            <dt className="text-muted">{SOURCE_LABELS[row.entity_type]}</dt>
                            <dd className={cn("font-mono", row.missing || row.stale ? "text-warning" : "text-secondary")}>
                                {row.indexed} / {row.total}
                            </dd>
                        </div>
                    ))}
                </dl>
                {Boolean(status?.file_issues?.length) && (
                    <div className="mt-3 space-y-2 text-warning">
                        <p>Не весь текст файлов доступен:</p>
                        {status?.file_issues.map((issue) => (
                            <p key={issue.source_id}><span className="font-medium">{issue.title}</span>: {issue.detail || "Ожидает обработки"}</p>
                        ))}
                    </div>
                )}
            </section>
        )}
        <section className="border-t border-line-subtle px-1 pt-4 text-[12px] leading-relaxed text-muted">
            <p className="mb-2 font-medium text-secondary">Что знает Арис</p>
            <ul className="flex list-disc flex-col gap-1 pl-4">
                <li>паспорт, команда, стадии и структура проекта;</li>
                <li>задачи, чек-листы, риски, вехи и стикеры;</li>
                <li>документы, комментарии и извлечённый текст файлов;</li>
                <li>связи объектов, история изменений и сохранённые отчёты.</li>
            </ul>
        </section>
    </>;
}
