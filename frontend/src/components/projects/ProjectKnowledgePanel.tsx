import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, BookOpen, GitBranch, Search } from "lucide-react";
import { Button, LinkButton } from "@/components/ui/Button";
import { Card, Section } from "@/components/ui/Card";
import { Field, Input, Select } from "@/components/ui/Field";
import { ErrorMessage, Skeleton } from "@/components/ui/States";
import { api, queryKeys } from "@/lib/api";
import { contextApi, KNOWLEDGE_STATES, type KnowledgeMap, type KnowledgeMapStatus } from "@/lib/projectKnowledge";
import { PROJECT_CONTEXT_FIELDS, projectContextLabel } from "@/lib/projectContext";
import { useCurrentUser } from "@/lib/useAuth";
import { useUiStore } from "@/stores/ui";
import { ProjectGitSources } from "./ProjectGitSources";

const VERIFICATION: Record<string, string> = {
    owner_statement: "Утверждение владельца", source_statement: "Сведения источника",
    participant_report: "Отчёт участника", status_only: "Только статус задачи",
    analysis: "Вывод анализа", unknown: "Подтверждение неизвестно",
};
const CATEGORY: Record<string, string> = { relations: "Связи", task_results: "Результаты задач" };
const TOPICS = [
    ["", "Все разделы"], ["goal", "Цели"], ["boundaries", "Границы"], ["decisions", "Решения"],
    ["task_results", "Результаты задач"], ["open_questions", "Открытые вопросы"],
] as const;

export function ProjectKnowledgeSummary({ projectId, projectKey }: { projectId: number; projectKey: string }) {
    const status = useQuery({
        queryKey: [...queryKeys.project(projectId), "knowledge-map", "status"],
        queryFn: () => api.get<KnowledgeMapStatus>(contextApi.status(projectId)),
    });
    return <Card className="flex flex-wrap items-center justify-between gap-4 p-4 sm:p-5">
        <div className="flex min-w-0 items-start gap-3">
            <BookOpen size={20} className="mt-0.5 shrink-0 text-accent" aria-hidden="true" />
            <div>
                <h2 className="text-[15px] font-semibold text-primary">Знания проекта</h2>
                {status.data?.coverage && <p className="mt-1 text-[13px] text-muted">
                    {KNOWLEDGE_STATES[status.data.status]} · {status.data.coverage.sources_current} из {status.data.coverage.sources_total} источников обработано
                </p>}
            </div>
        </div>
        <LinkButton to={`/projects/${projectKey}/knowledge`}>Открыть карту</LinkButton>
    </Card>;
}

export function ProjectKnowledgePanel({ projectId, projectKey, ownerId }: { projectId: number; projectKey: string; ownerId: number | null }) {
    const [offset, setOffset] = useState(0);
    const [search, setSearch] = useState("");
    const [queryText, setQueryText] = useState("");
    const [category, setCategory] = useState("");
    const [codeOpen, setCodeOpen] = useState(false);
    const [codeVisited, setCodeVisited] = useState(false);
    const currentUser = useCurrentUser();
    const openTask = useUiStore(state => state.setSelectedTaskId);
    const queryClient = useQueryClient();
    const query = useQuery({
        queryKey: [...queryKeys.project(projectId), "knowledge-map", offset, category, queryText],
        queryFn: () => api.get<KnowledgeMap>(contextApi.map(projectId, offset, category, queryText)),
        refetchInterval: (query) => ["pending", "updating"].includes(query.state.data?.status ?? "") ? 3000 : 15000,
    });
    const refresh = useMutation({
        mutationFn: () => api.post(contextApi.refresh(projectId)),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: [...queryKeys.project(projectId), "knowledge-map"] }),
    });
    const map = query.error ? undefined : query.data;
    const groups = new Map<string, KnowledgeMap["items"]>();
    for (const claim of map?.items ?? []) {
        const group = groups.get(claim.category);
        if (group) group.push(claim);
        else groups.set(claim.category, [claim]);
    }
    return <Section title="Карта проекта" action={currentUser.data?.id === ownerId && ownerId !== null
        ? <Button size="sm" onClick={() => refresh.mutate()} disabled={refresh.isPending}>Обновить знания</Button> : undefined}>
        <div className="grid min-w-0 items-start gap-4 lg:grid-cols-[14rem_minmax(0,1fr)]">
        <aside className="order-last flex min-w-0 flex-col gap-3 lg:order-none" aria-label="Разделы и охват карты">
            <Card className="hidden p-3 lg:block">
                <nav aria-label="Темы знаний" className="grid gap-1">
                    {TOPICS.map(([id, label]) => <Button key={id} variant="ghost" aria-pressed={category === id}
                        className="justify-between text-left aria-pressed:bg-accent-soft aria-pressed:text-accent"
                        onClick={() => { setCategory(id); setOffset(0); }}>
                        {label}<ArrowUpRight size={13} aria-hidden="true" />
                    </Button>)}
                </nav>
            </Card>
            {map && <Card className="space-y-3 p-4 text-[13px]">
                <h3 className="font-semibold text-primary">Охват карты</h3>
                <p className="font-medium text-secondary">{KNOWLEDGE_STATES[map.status] ?? map.status}</p>
                <dl className="space-y-1 text-muted">
                    <div className="flex flex-wrap justify-between gap-x-2"><dt>Источники</dt><dd>{map.coverage.sources_current} из {map.coverage.sources_total}</dd></div>
                    <div className="flex flex-wrap justify-between gap-x-2"><dt>{category || queryText ? "По фильтру" : "Утверждения на карте"}</dt><dd>{map.total}</dd></div>
                </dl>
                {!map.coverage.complete && <p className="text-muted">Карта пока охватывает не все материалы.</p>}
                {map.updated_at && <time dateTime={map.updated_at} className="block text-[12px] text-muted">{new Date(map.updated_at).toLocaleString("ru-RU")}</time>}
                {map.gaps.length > 0 && <details className="border-t border-line-subtle pt-2">
                    <summary className="touch-target cursor-pointer font-medium text-warning">Пробелы и ограничения ({map.gaps.length})</summary>
                    <ul className="mt-2 list-disc space-y-2 pl-5 text-muted">{map.gaps.map((gap, index) => <li key={`${gap.code}-${index}`}>{gap.message}</li>)}</ul>
                </details>}
            </Card>}
        </aside>
        <Card className="flex min-w-0 flex-col gap-4 p-4 text-[13px] sm:p-5">
            {query.isPending && <Skeleton className="h-20" />}
            {(query.error || refresh.error) && <ErrorMessage message={(query.error ?? refresh.error)!.message}
                action={<Button size="sm" onClick={() => void query.refetch()}>Повторить загрузку</Button>} />}
            <div>
                <Button aria-expanded={codeOpen} variant="ghost" icon={<GitBranch size={16} aria-hidden="true" />} onClick={() => { setCodeVisited(true); setCodeOpen(!codeOpen); }}>
                    Код и репозитории
                </Button>
                {codeVisited && <div hidden={!codeOpen} className="mt-3"><ProjectGitSources projectId={projectId} canEdit={false} /></div>}
            </div>
            <form className="flex flex-wrap items-end gap-3 border-y border-line-subtle py-3" onSubmit={event => {
                event.preventDefault(); setQueryText(search.trim()); setOffset(0);
            }}>
                <Field label="Поиск по утверждениям" className="min-w-0 flex-1 basis-48">{id =>
                    <Input id={id} type="search" maxLength={200} value={search} onChange={event => setSearch(event.target.value)} />
                }</Field>
                <Field label="Раздел" className="min-w-0 flex-1 basis-40 sm:grow-0">{id =>
                    <Select id={id} value={category} onChange={event => { setCategory(event.target.value); setOffset(0); }}>
                        <option value="">Все разделы</option>
                        {[...PROJECT_CONTEXT_FIELDS, ...Object.entries(CATEGORY)].map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                    </Select>
                }</Field>
                <Button size="sm" type="submit" icon={<Search size={14} aria-hidden="true" />}>Найти</Button>
                {(queryText || category) && <Button size="sm" onClick={() => { setSearch(""); setQueryText(""); setCategory(""); setOffset(0); }}>Сбросить</Button>}
            </form>
            {map && <>
                {map.items.length === 0 && <p className="text-muted">{queryText || category ? "По заданным условиям утверждений нет." : "Пока нет актуальных утверждений. Добавьте паспорт или документы проекта и запросите обновление."}</p>}
                {[...groups].map(([group, claims]) => <section key={group} className="min-w-0 overflow-hidden rounded-[var(--radius-card)] border border-line-subtle">
                    <h3 className="border-b border-line-subtle bg-accent-soft px-4 py-2.5 font-semibold text-primary">{CATEGORY[group] ?? projectContextLabel(group)}</h3>
                    <ul className="divide-y divide-line-subtle px-4">{claims.map(claim => <li key={claim.id} className="min-w-0 py-3">
                    <div className="mb-1 flex flex-wrap gap-x-3 gap-y-1 text-[12px] text-muted">
                        <span>{VERIFICATION[claim.verification] ?? claim.verification}</span>
                        {claim.state === "conflicting" && <strong className="text-warning">Есть противоречие</strong>}
                    </div>
                    <p className="whitespace-pre-wrap break-words text-secondary">{claim.text ?? "Текст недоступен при текущем доступе или версии."}</p>
                    {claim.evidence.length > 0 && <details className="mt-2">
                        <summary className="touch-target cursor-pointer text-accent">Источники и версии ({claim.evidence.length})</summary>
                        <ul className="mt-2 grid gap-3 xl:grid-cols-2">{claim.evidence.map((evidence, index) => <li key={`${evidence.source_id}-${index}`} className="min-w-0 rounded-md border border-line p-3">
                            <p className="font-medium text-secondary">{evidence.title}</p>
                            <p className="break-all text-[12px] text-muted">{evidence.source_id} · версия {evidence.native_revision ?? evidence.source_hash.slice(0, 12)} · {evidence.coverage}</p>
                            {evidence.quote && <blockquote className="mt-2 whitespace-pre-wrap break-words border-l-2 border-line pl-3 text-secondary">{evidence.quote}</blockquote>}
                            {evidence.document_ref ? <Link className="mt-2 inline-flex min-h-11 items-center text-accent hover:underline" to={`/projects/${projectKey}/docs/${encodeURIComponent(evidence.document_ref)}`}>Открыть документ</Link>
                                : evidence.task_id !== null ? <Button size="sm" className="mt-2" onClick={() => openTask(evidence.task_id)}>Открыть задачу</Button>
                                    : evidence.source_id === `project:${projectId}` ? <Link className="mt-2 inline-flex min-h-11 items-center text-accent hover:underline" to={`/projects/${projectKey}/settings`}>Открыть паспорт проекта</Link> : null}
                        </li>)}</ul>
                    </details>}
                    </li>)}</ul>
                </section>)}
                {(offset > 0 || map.has_more) && <nav aria-label="Страницы знаний проекта" className="flex gap-2">
                    <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>Назад</Button>
                    <Button size="sm" disabled={map.next_offset === null} onClick={() => map.next_offset !== null && setOffset(map.next_offset)}>Дальше</Button>
                </nav>}
            </>}
        </Card>
        </div>
    </Section>;
}
