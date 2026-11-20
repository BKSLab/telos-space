import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, BookOpen, Search } from "lucide-react";
import { Button, LinkButton } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Drawer, DrawerSection } from "@/components/ui/Drawer";
import { Input, Select } from "@/components/ui/Field";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";
import { api, queryKeys } from "@/lib/api";
import { contextApi, KNOWLEDGE_STATES, type KnowledgeClaim, type KnowledgeMap, type KnowledgeMapStatus } from "@/lib/projectKnowledge";
import { PROJECT_CONTEXT_FIELDS, projectContextLabel } from "@/lib/projectContext";
import { useCurrentUser } from "@/lib/useAuth";
import { useUiStore } from "@/stores/ui";
import { copyText } from "@/lib/clipboard";

const VERIFICATION: Record<string, string> = {
    owner_statement: "Утверждение владельца", source_statement: "Сведения источника",
    participant_report: "Отчёт участника", status_only: "Только статус задачи",
    analysis: "Вывод анализа", unknown: "Подтверждение неизвестно",
};
const CATEGORY: Record<string, string> = { relations: "Связи", task_results: "Результаты задач" };
const TOPICS: ReadonlyArray<readonly [string, string]> = [["", "Все разделы"], ...PROJECT_CONTEXT_FIELDS, ...Object.entries(CATEGORY)];
const categoryLabel = (value: string) => CATEGORY[value] ?? projectContextLabel(value);

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

function Highlight({ text, query }: { text: string; query: string }) {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return text;
    const lower = text.toLocaleLowerCase();
    const parts = [];
    let start = 0;
    let index = lower.indexOf(needle);
    while (index !== -1) {
        parts.push(text.slice(start, index), <mark key={index} className="rounded-sm bg-accent-soft text-primary">{text.slice(index, index + needle.length)}</mark>);
        start = index + needle.length;
        index = lower.indexOf(needle, start);
    }
    parts.push(text.slice(start));
    return <>{parts}</>;
}

export function ProjectKnowledgePanel({ projectId, projectKey, ownerId }: { projectId: number; projectKey: string; ownerId: number | null }) {
    const [params, setParams] = useSearchParams();
    const queryText = params.get("mq")?.slice(0, 200) ?? "";
    const category = params.get("category") ?? "";
    const requestedOffset = Number(params.get("map_offset"));
    const offset = Number.isSafeInteger(requestedOffset) && requestedOffset > 0 ? requestedOffset : 0;
    const claimId = params.get("claim");
    const [edit, setEdit] = useState<{ key: string; value: string }>();
    const search = edit?.key === queryText ? edit.value : queryText;
    const [copyMessage, setCopyMessage] = useState("");
    const currentUser = useCurrentUser();
    const openTask = useUiStore(state => state.setSelectedTaskId);
    const selectedTask = useUiStore(state => state.selectedTaskId);
    const queryClient = useQueryClient();
    function navigate(changes: Record<string, string | null>, replace = false) {
        const next = new URLSearchParams(params);
        for (const [key, value] of Object.entries(changes)) if (value) next.set(key, value); else next.delete(key);
        setParams(next, { replace });
    }
    const query = useQuery({
        queryKey: [...queryKeys.project(projectId), "knowledge-map", offset, category, queryText],
        queryFn: () => api.get<KnowledgeMap>(contextApi.map(projectId, offset, category, queryText)),
        refetchInterval: query => ["pending", "updating"].includes(query.state.data?.status ?? "") ? 3000 : 15000,
    });
    const refresh = useMutation({
        mutationFn: () => api.post(contextApi.refresh(projectId)),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: [...queryKeys.project(projectId), "knowledge-map"] }),
    });
    const map = query.error ? undefined : query.data;
    const selected = useQuery({
        queryKey: [...queryKeys.project(projectId), "knowledge-map", "claim", claimId],
        queryFn: () => api.get<KnowledgeClaim>(`/api/v1/projects/${projectId}/knowledge/map/claims/${encodeURIComponent(claimId!)}`),
        enabled: !!claimId,
        retry: false,
    });
    const claim = selected.error ? undefined : selected.data;
    const visibleClaim = claim?.category !== "facts" ? claim : undefined;
    const claimIndex = map?.items.findIndex(item => item.id === claimId) ?? -1;
    const updating = refresh.isPending || ["pending", "updating"].includes(map?.status ?? "");
    const groups = new Map<string, KnowledgeClaim[]>();
    for (const item of map?.items ?? []) groups.set(item.category, [...(groups.get(item.category) ?? []), item]);
    function chooseClaim(id: string) { setCopyMessage(""); navigate({ claim: id }); }
    const close = () => navigate({ claim: null }, true);
    return <div className="min-w-0 space-y-3">
        {map && <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-[12px] text-muted">
            <div className="min-w-0">
                <span className="text-secondary">Обработано {map.coverage.sources_current} из {map.coverage.sources_total} источников</span>
                <span> · {KNOWLEDGE_STATES[map.status] ?? map.status}</span>
                {map.updated_at && <time dateTime={map.updated_at} className="hidden sm:inline"> · {new Date(map.updated_at).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</time>}
            </div>
            {currentUser.data?.id === ownerId && ownerId !== null && <Button size="sm" variant="ghost" disabled={updating} onClick={() => refresh.mutate()}>{updating ? "Обновление знаний…" : "Обновить знания"}</Button>}
            {(!map.coverage.complete || map.gaps.length > 0) && <details className="w-full">
                <summary className="touch-target inline-flex cursor-pointer items-center text-warning">Пробелы и ограничения ({map.gaps.length})</summary>
                <div className="border-l-2 border-warning/40 py-2 pl-3">
                    {!map.coverage.complete && <p>Карта пока охватывает не все материалы.</p>}
                    <ul className="mt-1 space-y-1">{map.gaps.map((gap, index) => <li key={index}>{gap.message}</li>)}</ul>
                    {map.updated_at && <p className="mt-2 sm:hidden">Обновлено {new Date(map.updated_at).toLocaleString("ru-RU")}</p>}
                </div>
            </details>}
        </div>}
        {refresh.error && <ErrorMessage message={refresh.error.message} action={<Button size="sm" disabled={updating} onClick={() => refresh.mutate()}>Повторить обновление</Button>} />}
        <div className="grid min-w-0 items-start gap-4 lg:grid-cols-[13rem_minmax(0,1fr)]">
            <nav aria-label="Темы знаний" className="sticky top-3 hidden max-h-[75vh] overflow-y-auto border-r border-line-subtle pr-3 lg:grid">
                {TOPICS.map(([id, label]) => <Button key={id} variant="ghost" aria-pressed={category === id}
                    className="h-auto min-h-9 justify-start whitespace-normal text-left aria-pressed:bg-accent-soft aria-pressed:text-accent"
                    onClick={() => navigate({ category: id, map_offset: null, claim: null })}>{label}</Button>)}
            </nav>
            <div className="min-w-0 space-y-3">
                <form className="flex min-w-0 flex-wrap items-center gap-2" onSubmit={event => {
                    event.preventDefault(); navigate({ mq: search.trim(), map_offset: null, claim: null }); setEdit(undefined);
                }}>
                    <Input aria-label="Поиск по утверждениям" placeholder="Найти решение, цель, результат…" type="search" maxLength={200}
                        className="min-w-0 flex-1 basis-36" value={search} onChange={event => setEdit({ key: queryText, value: event.target.value })} />
                    <Button size="sm" type="submit" icon={<Search size={14} aria-hidden="true" />}>Найти</Button>
                    <Select aria-label="Раздел" className="basis-full lg:hidden" value={category} onChange={event => navigate({ category: event.target.value, map_offset: null, claim: null })}>
                        {TOPICS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                    </Select>
                    {(queryText || category) && <div className="flex w-full flex-wrap items-center gap-2 text-[12px]">
                        {queryText && <span className="break-words text-secondary">Поиск: «{queryText}»</span>}
                        {category && <span className="text-secondary">{categoryLabel(category)}</span>}
                        <Button variant="ghost" size="sm" onClick={() => { navigate({ mq: null, category: null, map_offset: null, claim: null }); setEdit(undefined); }}>Сбросить</Button>
                    </div>}
                </form>
                {query.isPending && <div role="status" aria-label="Загрузка знаний"><Skeleton className="h-24" /></div>}
                {query.error && <ErrorMessage message={query.error.message} action={<Button size="sm" onClick={() => void query.refetch()}>Повторить загрузку</Button>} />}
                {map && <>
                    <p className="text-[12px] text-muted" aria-live="polite">{map.total ? `${offset + 1}–${Math.min(offset + map.items.length, map.total)} из ${map.total}` : "0"} утверждений</p>
                    {map.items.length === 0 && <EmptyState title={queryText || category ? "По заданным условиям утверждений нет." : "Карта пока пуста"}
                        description={queryText || category ? "Измените запрос или сбросьте фильтры." : "Добавьте паспорт или документы проекта. После обработки здесь появятся знания и ссылки на источники."} />}
                    {[...groups].map(([group, claims]) => <section key={group} className="min-w-0">
                        <h3 className="border-b border-line-subtle py-2 text-[13px] font-semibold text-secondary">{categoryLabel(group)}</h3>
                        <ul className="divide-y divide-line-subtle">{claims.map(item => <li key={item.id} className="min-w-0">
                            <button type="button" className="group w-full rounded-md px-2 py-3 text-left hover:bg-hover focus-visible:outline-2 focus-visible:outline-accent"
                                onClick={() => chooseClaim(item.id)} aria-label={`Источники и версии: ${item.text ?? "Текст недоступен"}`}>
                                <p className="max-w-[80ch] whitespace-pre-wrap break-words text-[14px] leading-relaxed text-primary"><Highlight text={item.text ?? "Текст недоступен при текущем доступе или версии."} query={queryText} /></p>
                                <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-muted">
                                    <span>{VERIFICATION[item.verification] ?? item.verification}</span>
                                    <span className="text-accent group-hover:underline">Источники и версии ({item.evidence.length})</span>
                                    {item.state === "conflicting" && <strong className="inline-flex items-center gap-1 text-warning"><AlertTriangle size={13} aria-hidden="true" />Есть противоречие</strong>}
                                </div>
                            </button>
                        </li>)}</ul>
                    </section>)}
                    {(offset > 0 || map.has_more) && <nav aria-label="Страницы знаний проекта" className="flex gap-2">
                        <Button size="sm" disabled={offset === 0} onClick={() => navigate({ map_offset: String(Math.max(0, offset - 20)), claim: null })}>Назад</Button>
                        <Button size="sm" disabled={map.next_offset === null} onClick={() => navigate({ map_offset: String(map.next_offset), claim: null })}>Дальше</Button>
                    </nav>}
                </>}
            </div>
        </div>
        <Drawer label="Источники знания" header={<h3 className="text-[15px] font-semibold text-primary">Источники знания</h3>}
            isOpen={!!claimId && selectedTask === null} onClose={close} closeLabel="Закрыть источники"
            footer={<div className="flex flex-wrap items-center gap-2">
                {claimIndex >= 0 && map && <>
                    <Button size="sm" disabled={claimIndex === 0} onClick={() => chooseClaim(map.items[claimIndex - 1].id)}>Предыдущее</Button>
                    <Button size="sm" disabled={claimIndex === map.items.length - 1} onClick={() => chooseClaim(map.items[claimIndex + 1].id)}>Следующее</Button>
                </>}
                <Button size="sm" onClick={() => { void copyText(window.location.href).then(() => setCopyMessage("Ссылка скопирована"), () => setCopyMessage("Не удалось скопировать ссылку")); }}>Скопировать ссылку</Button>
                <span role="status" className="text-[12px] text-muted">{copyMessage}</span>
            </div>}>
            {selected.isPending && <div className="p-5"><Skeleton className="h-24" /></div>}
            {selected.error && <div className="p-5"><ErrorMessage message="Знание недоступно или было удалено." action={<Button onClick={() => void selected.refetch()}>Повторить загрузку</Button>} /></div>}
            {!selected.isPending && !selected.error && !visibleClaim && <p className="p-5 text-sm text-muted">Это знание больше не показывается на карте.</p>}
            {visibleClaim && <>
                <DrawerSection title={categoryLabel(visibleClaim.category)}>
                    <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-primary">{visibleClaim.text ?? "Текст недоступен при текущем доступе или версии."}</p>
                    <p className="mt-2 text-[12px] text-muted">{VERIFICATION[visibleClaim.verification] ?? visibleClaim.verification}</p>
                    {visibleClaim.state === "conflicting" && <p className="mt-2 text-sm text-warning">Есть противоречие. Сопоставьте источники перед использованием.</p>}
                    {visibleClaim.reason && <p className="mt-2 text-sm text-muted">{visibleClaim.reason}</p>}
                </DrawerSection>
                {visibleClaim.evidence.length === 0 && <p className="p-5 text-sm text-muted">Доступных источников нет.</p>}
                {visibleClaim.evidence.map((evidence, index) => <DrawerSection key={`${evidence.source_id}-${index}`} title={`Источник ${index + 1}`}>
                    <p className="break-words text-sm font-medium text-primary">{evidence.title}</p>
                    <p className="mt-1 text-[12px] text-muted">Версия {evidence.native_revision ?? "по снимку"} · {evidence.coverage === "full" ? "Полный охват" : "Неполный охват"}</p>
                    {evidence.quote && <blockquote className="mt-3 whitespace-pre-wrap break-words border-l-2 border-accent-border pl-3 text-sm leading-relaxed text-secondary">{evidence.quote}</blockquote>}
                    {evidence.document_ref ? <Link className="touch-target mt-2 inline-flex items-center text-sm text-accent hover:underline" to={`/projects/${projectKey}/docs/${encodeURIComponent(evidence.document_ref)}`}>Открыть документ</Link>
                        : evidence.task_id !== null ? <Button size="sm" className="mt-3" onClick={() => openTask(evidence.task_id)}>Открыть задачу</Button>
                            : evidence.source_id === `project:${projectId}` ? <Link className="touch-target mt-2 inline-flex items-center text-sm text-accent hover:underline" to={`/projects/${projectKey}/settings`}>Открыть паспорт проекта</Link> : null}
                    <details className="mt-3 text-[12px] text-muted"><summary className="touch-target cursor-pointer">Технические подробности</summary>
                        <p className="mt-2 break-all">{evidence.source_id} · версия {evidence.native_revision ?? evidence.source_hash.slice(0, 12)}</p>
                        <p className="break-all">SHA-256: {evidence.source_hash}</p>
                        <p>Проверено {new Date(evidence.checked_at).toLocaleString("ru-RU")}</p>
                    </details>
                </DrawerSection>)}
            </>}
        </Drawer>
    </div>;
}
