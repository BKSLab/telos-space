import { useEffect, useRef, useState, type CSSProperties, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowRight, CalendarDays, ChevronDown, CircleDot, ClipboardList, FileText, Flag, Link2, MessageSquare, Search, ShieldAlert, X } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/Field";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";
import { UserSelect } from "@/components/users/UserSelect";
import { UserIdentity } from "@/components/users/UserIdentity";
import { useUiStore } from "@/stores/ui";
import { projectContextLabel } from "@/lib/projectContext";
import { fullName, type ProjectMember, type Task } from "@/lib/types";
import { historyDayKey, historyPeriod, historyValue } from "@/lib/projectHistory";
import { copyText } from "@/lib/clipboard";

const LABELS = {
    task_activity: { label: "Задачи", icon: ClipboardList },
    task_comment: { label: "Комментарии", icon: MessageSquare },
    task_report: { label: "Отчёты", icon: FileText },
    project_deadline: { label: "Сроки проекта", icon: CalendarDays },
    project_context: { label: "Паспорт проекта", icon: Flag },
    risk_created: { label: "Добавление рисков", icon: ShieldAlert },
    document_created: { label: "Добавление документов", icon: FileText },
};
const TONES: Record<string, string> = {
    task_activity: "var(--color-accent)", task_comment: "var(--brand-cyan)",
    task_report: "var(--color-purple)", project_deadline: "var(--color-warning)",
    project_context: "var(--brand-violet)", risk_created: "var(--color-danger)",
    document_created: "var(--color-success)",
};
const emptyFilters = { q: "", kind: "", actor: "", actor_user_id: "", task_id: "", since: "", until: "" };
const FILTER_FIELDS = Object.keys(emptyFilters) as (keyof typeof emptyFilters)[];
const PAGE_SIZE = 20;
type Filters = typeof emptyFilters;
function eventCount(count: number) {
    const tail = count % 100;
    const word = tail >= 11 && tail <= 14 ? "событий" : count % 10 === 1 ? "событие" : count % 10 >= 2 && count % 10 <= 4 ? "события" : "событий";
    return `${count} ${word}`;
}

interface TimelineEvent {
    id: string; kind: string; title: string; detail: string | null; created_at: string;
    actor_name: string | null; actor_user_id?: number | null; task_id: number | null;
    document_ref?: string | null; from_value?: string | null; to_value?: string | null;
    comment_id?: number | null; report_id?: number | null; report_revision?: number | null; event_type?: string | null;
}
interface TimelinePage { items: TimelineEvent[]; next_offset: number | null }

function validTimezone(value: string) {
    try { new Intl.DateTimeFormat("ru", { timeZone: value }).format(); return true; } catch { return false; }
}
function eventDetail(event: TimelineEvent) {
    if (event.kind !== "project_context" || !event.detail) return event.detail;
    try {
        const fields: unknown = JSON.parse(event.detail);
        if (Array.isArray(fields) && fields.every(field => typeof field === "string")) return fields.map(projectContextLabel).join(", ");
    } catch { /* Incomplete JSON previews are not useful to the reader. */ }
    return null;
}
function EventDetail({ text }: { text: string }) {
    const [expanded, setExpanded] = useState(false);
    return <div className="mt-1 text-[13px] text-secondary">
        <p className={`max-w-[85ch] whitespace-pre-wrap break-words ${!expanded && text.length > 220 ? "line-clamp-2" : ""}`}>{text}</p>
        {text.length > 220 && <Button size="sm" variant="ghost" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? "Свернуть текст" : "Показать подробнее"}</Button>}
    </div>;
}

export function ProjectHistoryTimeline({ projectId, projectKey }: { projectId: number; projectKey: string }) {
    const openTask = useUiStore(state => state.setSelectedTaskId);
    const openTaskTarget = useUiStore(state => state.openTaskTarget);
    const [params, setParams] = useSearchParams();
    const [advanced, setAdvanced] = useState(false);
    const [copyStatus, setCopyStatus] = useState("");
    const listRef = useRef<HTMLDivElement>(null);
    const filters = Object.fromEntries(FILTER_FIELDS.map(key => [key, params.get(key)?.slice(0, 200) ?? ""])) as Filters;
    const timezoneParam = params.get("timezone");
    const timezone = timezoneParam && validTimezone(timezoneParam) ? timezoneParam : Intl.DateTimeFormat().resolvedOptions().timeZone;
    const filterKey = JSON.stringify(filters);
    const [edit, setEdit] = useState<{ key: string; value: Filters }>();
    const draft = edit?.key === filterKey ? edit.value : filters;
    const setDraft = (value: Filters) => setEdit({ key: filterKey, value });
    const requestedOffset = Number(params.get("offset"));
    const offset = Number.isSafeInteger(requestedOffset) && requestedOffset > 0 ? requestedOffset : 0;
    const selectedEvent = params.get("event");
    const groupKey = `${projectId}:${filterKey}:${timezone}:${offset}:${selectedEvent ?? ""}`;
    const [dayDisplay, setDayDisplay] = useState<{ key: string; collapsed: string[]; expanded: string[] }>({ key: "", collapsed: [], expanded: [] });
    const collapsed = dayDisplay.key === groupKey ? dayDisplay.collapsed : [];
    const expanded = dayDisplay.key === groupKey ? dayDisplay.expanded : [];
    const changeDays = (nextCollapsed: string[], nextExpanded = expanded) => setDayDisplay({ key: groupKey, collapsed: nextCollapsed, expanded: nextExpanded });
    const invalidDates = !!(draft.since && draft.until && draft.since > draft.until);
    const filtered = Object.values(filters).some(Boolean);
    const results = useQuery({
        queryKey: [...queryKeys.project(projectId), "history", filters, offset, timezone, selectedEvent],
        queryFn: () => {
            const query = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(selectedEvent ? 0 : offset), timezone });
            if (selectedEvent) query.set("event_id", selectedEvent);
            else for (const [key, value] of Object.entries(filters)) if (value) query.set(key, value);
            return api.get<TimelinePage>(`/api/v1/projects/${projectId}/history?${query}`);
        },
    });
    const page = results.error ? undefined : results.data;
    const members = useQuery({ queryKey: queryKeys.projectMembers(projectId), queryFn: () => api.get<ProjectMember[]>(endpoints.projectMembers(projectId)), enabled: advanced || !!filters.actor_user_id });
    const tasks = useQuery({ queryKey: queryKeys.tasks(projectId), queryFn: () => api.get<Task[]>(endpoints.projectTasks(projectId)), enabled: advanced || !!filters.task_id });
    const users = (members.data ?? []).map(member => member.user);
    const selectedUser = users.find(user => String(user.id) === draft.actor_user_id);
    const selectedTask = (tasks.data ?? []).find(task => String(task.id) === filters.task_id);
    const formatDay = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", year: "numeric", timeZone: timezone });
    const formatTime = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: timezone });
    const today = historyPeriod(1, timezone).until;
    const yesterday = historyPeriod(2, timezone).since;

    function navigate(nextFilters: Filters, nextOffset = 0) {
        const next = new URLSearchParams(params);
        for (const field of FILTER_FIELDS) {
            const value = nextFilters[field].trim();
            if (value) next.set(field, value); else next.delete(field);
        }
        next.set("timezone", timezone);
        next.delete("event");
        if (nextOffset) next.set("offset", String(nextOffset)); else next.delete("offset");
        // Keep the applied draft while Router commits its navigation transition.
        // An immediate Enter/click must not resubmit the previous URL filters.
        setEdit({ key: filterKey, value: nextFilters });
        setParams(next);
    }
    function applyFilters(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if (!invalidDates) navigate(draft); }
    async function copyEvent(event: TimelineEvent) {
        const next = new URLSearchParams(params);
        next.set("view", "history"); next.set("event", event.id); next.set("timezone", timezone);
        const url = new URL(window.location.href); url.search = next.toString(); url.hash = "";
        try { await copyText(url.toString()); setCopyStatus("Ссылка на событие скопирована"); }
        catch { setCopyStatus("Не удалось скопировать ссылку"); }
    }
    useEffect(() => {
        if (selectedEvent && results.data?.items.length) listRef.current?.focus({ preventScroll: true });
    }, [selectedEvent, results.data]);

    const chips: { field: keyof Filters; label: string }[] = [];
    if (filters.q) chips.push({ field: "q", label: `Поиск: ${filters.q}` });
    if (filters.kind) chips.push({ field: "kind", label: LABELS[filters.kind as keyof typeof LABELS]?.label ?? filters.kind });
    if (filters.actor_user_id) chips.push({ field: "actor_user_id", label: `Автор: ${users.find(user => String(user.id) === filters.actor_user_id) ? fullName(users.find(user => String(user.id) === filters.actor_user_id)!) : `№ ${filters.actor_user_id}`}` });
    if (filters.actor) chips.push({ field: "actor", label: `Имя: ${filters.actor}` });
    if (filters.task_id) chips.push({ field: "task_id", label: selectedTask?.title ?? `Задача № ${filters.task_id}` });
    if (filters.since) chips.push({ field: "since", label: `С ${filters.since}` });
    if (filters.until) chips.push({ field: "until", label: `По ${filters.until}` });
    const days = new Map<string, TimelineEvent[]>();
    for (const event of page?.items ?? []) {
        const day = historyDayKey(new Date(event.created_at), timezone);
        days.set(day, [...(days.get(day) ?? []), event]);
    }

    return <div className="flex min-w-0 flex-col gap-4">
        <form onSubmit={applyFilters} className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-end gap-2 border-b border-line-subtle pb-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_auto] sm:gap-3">
            <Field label="Поиск по истории" className="col-span-2 sm:col-span-1">{id => <Input id={id} type="search" placeholder="Событие, задача или изменение…" maxLength={200} value={draft.q} onChange={event => setDraft({ ...draft, q: event.target.value })} />}</Field>
            <Field label="Тип события">{id => <Select id={id} value={draft.kind} onChange={event => { const next = { ...draft, kind: event.target.value }; setDraft(next); if (!invalidDates) navigate(next); }}>
                <option value="">Все события</option>{Object.entries(LABELS).map(([kind, { label }]) => <option key={kind} value={kind}>{label}</option>)}
            </Select>}</Field>
            <Button size="sm" type="submit" disabled={invalidDates} icon={<Search size={14} aria-hidden="true" />}>Найти</Button>
            <div className="col-span-2 flex flex-wrap items-center gap-1 sm:col-span-3">
                {[{ days: 1, label: "Сегодня" }, { days: 7, label: "7 дней" }, { days: 30, label: "30 дней" }].map(period => {
                    const range = historyPeriod(period.days, timezone);
                    return <Button key={period.days} size="sm" variant="ghost" aria-pressed={filters.since === range.since && filters.until === range.until} className="aria-pressed:bg-accent-soft aria-pressed:text-accent" onClick={() => navigate({ ...draft, ...range })}>{period.label}</Button>;
                })}
                <Button size="sm" variant="ghost" aria-expanded={advanced} onClick={() => setAdvanced(!advanced)}>Автор, задача и период</Button>
                {(filtered || Object.values(draft).some(Boolean)) && <Button size="sm" variant="ghost" onClick={() => navigate(emptyFilters)}>Сбросить</Button>}
            </div>
            {advanced && <div className="col-span-2 grid min-w-0 gap-3 sm:col-span-3 sm:grid-cols-2 lg:grid-cols-4">
                <Field label="Автор">{id => <UserSelect id={id} label="Автор" users={users} selected={selectedUser ? [selectedUser] : []} emptyLabel="Все авторы" clearLabel="Все авторы" loading={members.isPending} error={members.error?.message}
                    fallbackSelection={draft.actor_user_id && !selectedUser ? { userId: Number(draft.actor_user_id), name: `Автор № ${draft.actor_user_id}` } : undefined}
                    onChange={selection => setDraft({ ...draft, actor_user_id: selection[0] ? String(selection[0].id) : "", actor: "" })} />}</Field>
                <Field label="Задача" error={tasks.error?.message}>{id => <Select id={id} value={draft.task_id} onChange={event => setDraft({ ...draft, task_id: event.target.value })}>
                    <option value="">Все задачи</option>
                    {draft.task_id && !(tasks.data ?? []).some(task => String(task.id) === draft.task_id) && <option value={draft.task_id}>Задача № {draft.task_id}</option>}
                    {(tasks.data ?? []).map(task => <option key={task.id} value={task.id}>{task.title}</option>)}
                </Select>}</Field>
                <Field label="С даты">{id => <Input id={id} type="date" value={draft.since} onChange={event => setDraft({ ...draft, since: event.target.value })} />}</Field>
                <Field label="По дату" error={invalidDates ? "Укажите дату не раньше начала." : undefined}>{id => <Input id={id} type="date" value={draft.until} onChange={event => setDraft({ ...draft, until: event.target.value })} />}</Field>
                <Field label="Имя в старых записях">{id => <Input id={id} type="search" placeholder="Если автора нет в команде" maxLength={200} value={draft.actor} onChange={event => setDraft({ ...draft, actor: event.target.value, actor_user_id: "" })} />}</Field>
            </div>}
        </form>
        {!selectedEvent && chips.length > 0 && <div className="flex flex-wrap gap-1" aria-label="Применённые фильтры">{chips.map(chip => <Button key={chip.field} size="sm" variant="ghost" className="max-w-full bg-accent-soft text-accent" aria-label={`Убрать фильтр: ${chip.label}`} onClick={() => navigate({ ...filters, [chip.field]: "" })}><span className="truncate">{chip.label}</span><X size={13} aria-hidden="true" /></Button>)}</div>}
        {selectedEvent && <div className="flex flex-wrap items-center gap-2 text-[13px]"><span>Событие по ссылке</span><Button size="sm" onClick={() => navigate(filters, offset)}>Вернуться к ленте</Button></div>}
        <div className="flex flex-wrap items-baseline justify-between gap-2 text-[12px] text-muted">
            <details><summary className="cursor-pointer py-2">Какие события здесь видны</summary><p className="mt-2 max-w-3xl">Сохранённые изменения задач и паспорта, комментарии, версии отчётов, сроки проекта, добавление рисков и документов. После переноса или удаления задачи история может быть неполной. Код доступен в разделе «Код и репозитории».</p></details>
            <span>Время: {timezone}</span>
        </div>
        {copyStatus && <p role="status" className="text-[12px] text-muted">{copyStatus}</p>}
        {results.isPending && <div role="status" aria-label="Загрузка истории" className="space-y-3"><Skeleton className="h-20" /><Skeleton className="h-20" /></div>}
        {results.error && <ErrorMessage message={results.error.message} action={<Button size="sm" onClick={() => void results.refetch()}>Повторить</Button>} />}
        {page?.items.length === 0 && <EmptyState title={selectedEvent ? "Событие недоступно или удалено" : filtered ? "События по фильтрам не найдены" : "История пока пуста"} />}
        {days.size > 0 && <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line-subtle pb-2">
            <span className="text-[12px] text-muted">На странице: {eventCount(page!.items.length)} · дат: {days.size}</span>
            <div className="flex gap-1"><Button size="sm" variant="ghost" onClick={() => changeDays([...days.keys()])}>Свернуть даты</Button><Button size="sm" variant="ghost" onClick={() => changeDays([])}>Развернуть даты</Button></div>
        </div>}
        <div ref={listRef} tabIndex={-1} aria-label="События проекта" className="history-timeline outline-none">
            {[...days].map(([day, events]) => {
                const fullDay = formatDay.format(new Date(events[0].created_at));
                const isCollapsed = collapsed.includes(day);
                const allVisible = expanded.includes(day) || !!selectedEvent;
                const visibleEvents = allVisible ? events : events.slice(0, 4);
                const kinds = [...new Set(events.map(event => event.kind))];
                return <section key={day} className="history-day min-w-0 pb-6">
                    <h3 className="relative mb-3">
                        <button type="button" aria-expanded={!isCollapsed} aria-controls={`events-${day}`} aria-label={`${isCollapsed ? "Развернуть" : "Свернуть"} ${fullDay}`}
                            className="history-day-toggle flex w-full min-w-0 items-center gap-3 rounded-lg py-3 pr-3 text-left focus-visible:outline-2 focus-visible:outline-accent"
                            onClick={() => changeDays(isCollapsed ? collapsed.filter(value => value !== day) : [...collapsed, day])}>
                            <span className="history-milestone grid size-9 shrink-0 place-items-center rounded-full border border-accent bg-surface text-accent"><CalendarDays size={17} aria-hidden="true" /></span>
                            <span className="min-w-0 flex-1"><span className="block text-[15px] font-semibold text-primary">{day === today ? "Сегодня" : day === yesterday ? "Вчера" : fullDay}</span>
                                {(day === today || day === yesterday) && <span className="text-[12px] text-muted">{fullDay}</span>}
                            </span>
                            <span className="rounded-md bg-accent-soft px-2 py-1 text-[12px] font-medium text-accent">{eventCount(events.length)}</span>
                            <ChevronDown size={16} aria-hidden="true" className={`shrink-0 text-muted transition-transform ${isCollapsed ? "-rotate-90" : ""}`} />
                        </button>
                    </h3>
                    <div className="mb-3 ml-12 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted" aria-label={`Состав событий за ${fullDay}`}>
                        {kinds.map(kind => <span key={kind} className="inline-flex items-center gap-1.5"><span aria-hidden="true" className="size-1.5 rounded-full" style={{ backgroundColor: TONES[kind] ?? "var(--color-text-muted)" }} />{LABELS[kind as keyof typeof LABELS]?.label ?? "События"} · {events.filter(event => event.kind === kind).length}</span>)}
                    </div>
                    <div id={`events-${day}`} hidden={isCollapsed}>
                    <ol className="space-y-3">{visibleEvents.map(event => {
                        const { label, icon: Icon } = LABELS[event.kind as keyof typeof LABELS] ?? { label: "Событие", icon: CircleDot };
                        const detail = eventDetail(event);
                        const structured = event.from_value != null || event.to_value != null;
                        return <li key={event.id} id={`history-${event.id}`} style={{ "--history-tone": TONES[event.kind] ?? "var(--color-accent)" } as CSSProperties}
                            className="history-event relative grid min-w-0 grid-cols-[2.25rem_minmax(0,1fr)] gap-3">
                            <span className="history-event-marker z-10 mt-3 grid size-9 place-items-center rounded-full"><Icon size={16} aria-hidden="true" /></span>
                            <div className={`history-event-card min-w-0 rounded-lg p-3 sm:px-4 ${selectedEvent === event.id ? "ring-1 ring-accent" : ""}`}>
                                <div className="mb-2 flex items-center justify-between gap-2 text-[11px]">
                                    <span className="history-event-kind rounded px-2 py-0.5 font-medium">{label}</span>
                                    <time className="shrink-0 font-mono text-muted" dateTime={event.created_at} title={`${fullDay}, ${formatTime.format(new Date(event.created_at))} (${timezone})`}>{formatTime.format(new Date(event.created_at))}</time>
                                </div>
                                <p className="break-words text-[14px] font-medium text-primary">{event.title}</p>
                                {structured && <div className="mt-2 flex flex-wrap items-center gap-2 text-[12px]"><span className="max-w-full whitespace-pre-wrap break-words rounded bg-surface px-2 py-1 text-muted"><span className="sr-only">Было: </span>{historyValue(event.from_value, event.event_type)}</span><ArrowRight size={14} className="text-muted" aria-hidden="true" /><span className="history-event-kind max-w-full whitespace-pre-wrap break-words rounded px-2 py-1"><span className="sr-only">Стало: </span>{historyValue(event.to_value, event.event_type)}</span></div>}
                                {detail && !structured && <EventDetail text={detail} />}
                                {detail && structured && <details className="mt-1 text-[13px] text-muted"><summary className="cursor-pointer py-1">Подробности изменения</summary><EventDetail text={detail} /></details>}
                                <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-muted">
                                    {event.actor_name ? <UserIdentity userId={event.actor_user_id} name={event.actor_name} size="xs" /> : <span>Автор не указан</span>}
                                </div>
                                <div className="mt-1 flex flex-wrap items-center gap-1">
                                    {event.task_id !== null && <Button size="sm" variant="ghost" onClick={() => {
                                        if (event.report_id) openTaskTarget(event.task_id!, { section: "reports", reportId: event.report_id, reportRevision: event.report_revision ?? undefined });
                                        else if (event.comment_id) openTaskTarget(event.task_id!, { section: "comments", commentId: event.comment_id });
                                        else openTask(event.task_id);
                                    }}>{event.report_id ? "Открыть версию отчёта" : event.comment_id ? "Открыть комментарий" : "Открыть задачу"}</Button>}
                                    {event.document_ref && <Link className="touch-target inline-flex items-center px-2 text-[13px] text-accent hover:underline" to={`/projects/${projectKey}/docs/${encodeURIComponent(event.document_ref)}`}>Открыть документ</Link>}
                                    <Button size="sm" variant="ghost" aria-label={`Скопировать ссылку: ${event.title}`} onClick={() => void copyEvent(event)} icon={<Link2 size={13} aria-hidden="true" />}>Ссылка</Button>
                                </div>
                            </div>
                        </li>;
                    })}</ol>
                    {events.length > 4 && <div className="ml-12 mt-3"><Button size="sm" variant="ghost" onClick={() => changeDays(collapsed, allVisible ? expanded.filter(value => value !== day) : [...expanded, day])}>{allVisible ? "Показать первые 4" : `Ещё ${eventCount(events.length - 4)} за этот день`}</Button></div>}
                    </div>
                </section>;
            })}
        </div>
        {!selectedEvent && page && (offset > 0 || page.next_offset !== null) && <nav aria-label="Страницы истории проекта" className="flex flex-wrap items-center gap-2">
            <Button size="sm" disabled={!offset} onClick={() => navigate(filters, Math.max(0, offset - PAGE_SIZE))}>Новее</Button>
            <span className="text-[12px] text-muted">Страница {Math.floor(offset / PAGE_SIZE) + 1}</span>
            <Button size="sm" disabled={page.next_offset === null} onClick={() => { if (page.next_offset !== null) navigate(filters, page.next_offset); }}>Раньше</Button>
        </nav>}
    </div>;
}
