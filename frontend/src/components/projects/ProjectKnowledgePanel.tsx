import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/Button";
import { Card, Section } from "@/components/ui/Card";
import { ErrorMessage, Skeleton } from "@/components/ui/States";
import { api, queryKeys } from "@/lib/api";
import { contextApi, KNOWLEDGE_STATES, type KnowledgeMap } from "@/lib/projectKnowledge";
import { projectContextLabel } from "@/lib/projectContext";
import { useCurrentUser } from "@/lib/useAuth";
import { useUiStore } from "@/stores/ui";

const VERIFICATION: Record<string, string> = {
    owner_statement: "Утверждение владельца", source_statement: "Сведения источника",
    participant_report: "Отчёт участника", status_only: "Только статус задачи",
    analysis: "Вывод анализа", unknown: "Подтверждение неизвестно",
};
const CATEGORY: Record<string, string> = { facts: "Сведения", relations: "Связи", task_results: "Результаты задач" };

export function ProjectKnowledgePanel({ projectId, projectKey, ownerId }: { projectId: number; projectKey: string; ownerId: number | null }) {
    const [page, setPage] = useState({ projectId, offset: 0 });
    const offset = page.projectId === projectId ? page.offset : 0;
    const currentUser = useCurrentUser();
    const openTask = useUiStore(state => state.setSelectedTaskId);
    const queryClient = useQueryClient();
    const query = useQuery({
        queryKey: [...queryKeys.project(projectId), "knowledge-map", offset],
        queryFn: () => api.get<KnowledgeMap>(contextApi.map(projectId, offset)),
        refetchInterval: (query) => ["pending", "updating"].includes(query.state.data?.status ?? "") ? 3000 : 15000,
    });
    const refresh = useMutation({
        mutationFn: () => api.post(contextApi.refresh(projectId)),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: [...queryKeys.project(projectId), "knowledge-map"] }),
    });
    const map = query.error ? undefined : query.data;
    return <Section title="Знания проекта" action={currentUser.data?.id === ownerId && ownerId !== null
        ? <Button size="sm" onClick={() => refresh.mutate()} disabled={refresh.isPending}>Обновить знания</Button> : undefined}>
        <Card className="flex min-w-0 flex-col gap-3 p-4 text-[13px]">
            {query.isPending && <Skeleton className="h-20" />}
            {(query.error || refresh.error) && <ErrorMessage message={(query.error ?? refresh.error)!.message}
                action={<Button size="sm" onClick={() => void query.refetch()}>Повторить загрузку</Button>} />}
            {map && <>
                <div className="flex flex-wrap items-center justify-between gap-2 text-muted">
                    <p className="font-medium text-secondary">{KNOWLEDGE_STATES[map.status] ?? map.status}</p>
                    <p>{map.updated_at ? <time dateTime={map.updated_at}>{new Date(map.updated_at).toLocaleString("ru-RU")}</time> : "Обновление ещё не выполнено"}</p>
                </div>
                <p className="text-muted">Источников проверено: {map.coverage.sources_current} из {map.coverage.sources_total}.
                    {" "}Утверждений: {map.total}. {!map.coverage.complete && "Карта пока охватывает не все материалы."}</p>
                <p className="text-[12px] text-muted">Карта сохраняет сведения и цитаты источников. Слова участника и статус задачи сами по себе не подтверждают работу на проде.</p>
                {map.gaps.length > 0 && <details className="rounded-md border border-line p-3">
                    <summary className="touch-target cursor-pointer font-medium text-secondary">Пробелы и ограничения ({map.gaps.length})</summary>
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-muted">{map.gaps.map((gap, index) => <li key={`${gap.code}-${index}`}>{gap.message}</li>)}</ul>
                </details>}
                {map.items.length === 0 && <p className="text-muted">Пока нет актуальных утверждений. Добавьте паспорт или документы проекта и запросите обновление.</p>}
                <ul className="divide-y divide-line-subtle">{map.items.map(claim => <li key={claim.id} className="min-w-0 py-3">
                    <div className="mb-1 flex flex-wrap gap-x-3 gap-y-1 text-[12px] text-muted">
                        <span>{CATEGORY[claim.category] ?? projectContextLabel(claim.category)}</span><span>{VERIFICATION[claim.verification] ?? claim.verification}</span>
                        {claim.state === "conflicting" && <strong className="text-warning">Есть противоречие</strong>}
                    </div>
                    <p className="whitespace-pre-wrap break-words text-secondary">{claim.text ?? "Текст недоступен при текущем доступе или версии."}</p>
                    {claim.evidence.length > 0 && <details className="mt-2">
                        <summary className="touch-target cursor-pointer text-accent">Источники и версии ({claim.evidence.length})</summary>
                        <ul className="mt-2 space-y-3">{claim.evidence.map((evidence, index) => <li key={`${evidence.source_id}-${index}`} className="min-w-0 rounded-md border border-line p-3">
                            <p className="font-medium text-secondary">{evidence.title}</p>
                            <p className="break-all text-[12px] text-muted">{evidence.source_id} · версия {evidence.native_revision ?? evidence.source_hash.slice(0, 12)} · {evidence.coverage}</p>
                            {evidence.quote && <blockquote className="mt-2 whitespace-pre-wrap break-words border-l-2 border-line pl-3 text-secondary">{evidence.quote}</blockquote>}
                            {evidence.document_ref ? <Link className="mt-2 inline-flex min-h-11 items-center text-accent hover:underline" to={`/projects/${projectKey}/docs/${encodeURIComponent(evidence.document_ref)}`}>Открыть документ</Link>
                                : evidence.task_id !== null ? <Button size="sm" className="mt-2" onClick={() => openTask(evidence.task_id)}>Открыть задачу</Button>
                                    : evidence.source_id === `project:${projectId}` ? <Link className="mt-2 inline-flex min-h-11 items-center text-accent hover:underline" to={`/projects/${projectKey}/settings`}>Открыть паспорт проекта</Link> : null}
                        </li>)}</ul>
                    </details>}
                </li>)}</ul>
                {(offset > 0 || map.has_more) && <nav aria-label="Страницы знаний проекта" className="flex gap-2">
                    <Button size="sm" disabled={offset === 0} onClick={() => setPage({ projectId, offset: Math.max(0, offset - 20) })}>Назад</Button>
                    <Button size="sm" disabled={map.next_offset === null} onClick={() => map.next_offset !== null && setPage({ projectId, offset: map.next_offset })}>Дальше</Button>
                </nav>}
            </>}
        </Card>
    </Section>;
}
