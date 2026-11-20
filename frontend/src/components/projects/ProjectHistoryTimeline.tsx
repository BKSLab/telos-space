import { useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { CalendarDays, CircleDot, ClipboardList, FileText, Flag, MessageSquare, Search, ShieldAlert } from "lucide-react";
import { api, queryKeys } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field, Input, Select } from "@/components/ui/Field";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";
import { useUiStore } from "@/stores/ui";
import { projectContextLabel } from "@/lib/projectContext";

const LABELS = {
    task_activity: { label: "Задачи", icon: ClipboardList, tone: "border-accent-border bg-accent-soft text-accent" },
    task_comment: { label: "Комментарии", icon: MessageSquare, tone: "border-line bg-surface-2 text-secondary" },
    task_report: { label: "Отчёты", icon: FileText, tone: "border-line bg-surface-2 text-secondary" },
    project_deadline: { label: "Сроки проекта", icon: CalendarDays, tone: "border-accent-border bg-accent-soft text-accent" },
    project_context: { label: "Паспорт проекта", icon: Flag, tone: "border-accent-border bg-accent-soft text-accent" },
    risk_created: { label: "Добавление рисков", icon: ShieldAlert, tone: "border-line bg-surface-2 text-warning" },
    document_created: { label: "Добавление документов", icon: FileText, tone: "border-line bg-surface-2 text-secondary" },
};
const fallback = { label: "Событие", icon: CircleDot, tone: "border-line bg-surface-2 text-secondary" };
const emptyFilters = { q: "", kind: "", actor: "", since: "", until: "" };
const formatDay = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
const formatTime = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
const PAGE_SIZE = 20;
const FILTER_FIELDS = ["q", "kind", "actor", "since", "until"] as const;
type Filters = typeof emptyFilters;

interface TimelineEvent {
    id: string;
    kind: string;
    title: string;
    detail: string | null;
    created_at: string;
    actor_name: string | null;
    task_id: number | null;
    document_ref?: string | null;
}

interface TimelinePage {
    items: TimelineEvent[];
    next_offset: number | null;
}

function eventDetail(event: TimelineEvent) {
    if (event.kind !== "project_context" || !event.detail) return event.detail;
    try {
        const fields: unknown = JSON.parse(event.detail);
        if (Array.isArray(fields) && fields.every(field => typeof field === "string")) {
            return fields.map(projectContextLabel).join(", ");
        }
    } catch {
        // The server limits preview length. Do not show incomplete technical JSON.
    }
    return null;
}

export function ProjectHistoryTimeline({ projectId, projectKey }: { projectId: number; projectKey: string }) {
    const openTask = useUiStore(state => state.setSelectedTaskId);
    const [params, setParams] = useSearchParams();
    const filters: Filters = {
        q: params.get("q")?.slice(0, 200) ?? "",
        kind: params.get("kind") ?? "",
        actor: params.get("actor")?.slice(0, 200) ?? "",
        since: params.get("since") ?? "",
        until: params.get("until") ?? "",
    };
    const filterKey = JSON.stringify(filters);
    const [edit, setEdit] = useState<{ key: string; value: Filters }>();
    const draft = edit?.key === filterKey ? edit.value : filters;
    const setDraft = (value: Filters) => setEdit({ key: filterKey, value });
    const requestedOffset = Number(params.get("offset"));
    const offset = Number.isSafeInteger(requestedOffset) && requestedOffset > 0 ? requestedOffset : 0;
    const invalidDates = !!(draft.since && draft.until && draft.since > draft.until);
    const filtered = Object.values(filters).some(Boolean);
    const results = useQuery({
        queryKey: [...queryKeys.project(projectId), "history", filters, offset],
        queryFn: () => {
            const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
            for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
            return api.get<TimelinePage>(`/api/v1/projects/${projectId}/history?${params}`);
        },
    });

    function navigate(filters: Filters, offset = 0) {
        const next = new URLSearchParams(params);
        for (const field of FILTER_FIELDS) {
            const value = filters[field].trim();
            if (value) next.set(field, value);
            else next.delete(field);
        }
        if (offset) next.set("offset", String(offset));
        else next.delete("offset");
        setEdit(undefined);
        setParams(next);
    }

    function applyFilters(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        if (invalidDates) return;
        navigate(draft);
    }

    const days = new Map<string, TimelineEvent[]>();
    for (const event of results.data?.items ?? []) {
        const day = formatDay.format(new Date(event.created_at));
        const events = days.get(day);
        if (events) events.push(event);
        else days.set(day, [event]);
    }

    return <div className="flex min-w-0 flex-col gap-5">
        <Card className="p-4 sm:p-5">
            <form onSubmit={applyFilters} className="grid min-w-0 gap-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_auto] sm:items-end">
                <Field label="Поиск по истории">{id => <Input id={id} type="search" maxLength={200} value={draft.q} onChange={event => setDraft({ ...draft, q: event.target.value })} />}</Field>
                <Field label="Тип события">{id => <Select id={id} value={draft.kind} onChange={event => setDraft({ ...draft, kind: event.target.value })}>
                    <option value="">Все события</option>
                    {Object.entries(LABELS).map(([kind, { label }]) => <option key={kind} value={kind}>{label}</option>)}
                </Select>}</Field>
                <div className="flex flex-wrap items-center gap-2">
                    <Button size="sm" type="submit" disabled={invalidDates} icon={<Search size={14} aria-hidden="true" />}>Найти</Button>
                    {(filtered || Object.values(draft).some(Boolean)) && <Button size="sm" onClick={() => navigate(emptyFilters)}>Сбросить</Button>}
                </div>
                <details className="min-w-0 border-t border-line-subtle pt-2 text-[13px] sm:col-span-3">
                    <summary className="touch-target cursor-pointer font-medium text-secondary">
                        Автор и период{(filters.actor || filters.since || filters.until) && <span className="ml-2 text-accent">Фильтр применён</span>}
                    </summary>
                    <div className="mt-2 grid min-w-0 gap-3 sm:grid-cols-3">
                        <Field label="Автор">{id => <Input id={id} type="search" maxLength={200} value={draft.actor} onChange={event => setDraft({ ...draft, actor: event.target.value })} />}</Field>
                        <Field label="С даты">{id => <Input id={id} className="min-w-0" type="date" value={draft.since} onChange={event => setDraft({ ...draft, since: event.target.value })} />}</Field>
                        <Field label="По дату" error={invalidDates ? "Укажите дату не раньше начала." : undefined}>{id => <Input id={id} className="min-w-0" type="date" value={draft.until} onChange={event => setDraft({ ...draft, until: event.target.value })} />}</Field>
                    </div>
                </details>
            </form>
        </Card>
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 text-muted">
            <details className="text-[13px]">
                <summary className="touch-target cursor-pointer font-medium text-secondary">Какие события здесь видны</summary>
                <p className="mt-2 max-w-3xl">Лента охватывает сохранённые изменения задач и паспорта, комментарии, версии отчётов, сроки проекта, добавление рисков и документов. История может быть неполной после переноса или удаления задачи. Данные репозиториев доступны в карте.</p>
            </details>
            <span className="text-[12px]">Время в UTC</span>
        </div>
        {results.isPending && <div role="status" aria-label="Загрузка истории" className="space-y-3"><Skeleton className="h-20" /><Skeleton className="h-20" /></div>}
        {results.error && <ErrorMessage message={results.error.message} action={<Button size="sm" onClick={() => void results.refetch()}>Повторить</Button>} />}
        {results.data?.items.length === 0 && <EmptyState title={filtered ? "События по фильтрам не найдены" : "История пока пуста"} />}
        {[...days].map(([day, events]) => <section key={day} className="min-w-0 sm:grid sm:grid-cols-[10rem_minmax(0,1fr)] sm:gap-5">
            <h3 className="mb-3 flex items-center gap-2 border-t border-line-subtle pt-3 text-[13px] font-semibold text-secondary sm:self-start"><CalendarDays size={15} className="text-accent" aria-hidden="true" />{day}</h3>
            <ol className="relative before:absolute before:inset-y-5 before:left-[15px] before:w-px before:bg-linear-to-b before:from-accent/50 before:to-line motion-safe:animate-[overlay-in_var(--duration-normal)_var(--ease-standard)]">
                {events.map(event => {
                    const { label, icon: Icon, tone } = LABELS[event.kind as keyof typeof LABELS] ?? fallback;
                    const detail = eventDetail(event);
                    return <li key={event.id} className="relative grid min-w-0 grid-cols-[2rem_minmax(0,1fr)] items-start gap-3">
                        <span className={`z-10 mt-3 grid size-8 place-items-center rounded-full border ${tone}`}><Icon size={15} aria-hidden="true" /></span>
                        <div className="min-w-0 border-t border-line-subtle py-3 sm:px-2">
                            <div className="mb-1 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-[12px] text-muted">
                                <span><span className="font-medium text-secondary">{label}</span>{event.actor_name && <> · {event.actor_name}</>}</span>
                                <time dateTime={event.created_at} className="font-mono">{formatTime.format(new Date(event.created_at))}</time>
                            </div>
                            <p className="break-words text-[14px] font-medium text-primary">{event.title}</p>
                            {detail && <p className="mt-1 whitespace-pre-wrap break-words text-[13px] text-secondary">{detail}</p>}
                            {event.task_id !== null && <Button size="sm" variant="ghost" className="mt-2" onClick={() => openTask(event.task_id)}>Открыть задачу</Button>}
                            {event.document_ref && <Link className="touch-target mt-2 inline-flex items-center text-[13px] text-accent hover:underline" to={`/projects/${projectKey}/docs/${encodeURIComponent(event.document_ref)}`}>Открыть документ</Link>}
                        </div>
                    </li>;
                })}
            </ol>
        </section>)}
        {results.data && (offset > 0 || results.data.next_offset !== null) && <nav aria-label="Страницы истории проекта" className="flex flex-wrap gap-2">
            <Button size="sm" disabled={!offset} onClick={() => navigate(filters, Math.max(0, offset - PAGE_SIZE))}>Новее</Button>
            <Button size="sm" disabled={results.data.next_offset === null} onClick={() => { if (results.data.next_offset !== null) navigate(filters, results.data.next_offset); }}>Раньше</Button>
        </nav>}
    </div>;
}
