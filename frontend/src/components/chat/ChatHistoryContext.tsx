import { useEffect, useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, ApiError, queryKeys } from "@/lib/api";
import { historyDayKey, historyValue } from "@/lib/projectHistory";
import { browserTimezone } from "@/lib/timezone";
import { projectContextLabel } from "@/lib/projectContext";
import type { TimelineEvent } from "@/lib/useProjectHistory";
import { useUiStore } from "@/stores/ui";
import { UserIdentity } from "@/components/users/UserIdentity";
import { Button } from "@/components/ui/Button";
import { ErrorMessage, Skeleton } from "@/components/ui/States";

interface HistoryPage { items: TimelineEvent[]; next_offset: number | null }

function localToday() {
    const timezone = browserTimezone();
    return { timezone, day: historyDayKey(new Date(), timezone) };
}

function useToday() {
    const [today, setToday] = useState(localToday);
    useEffect(() => {
        let timer: ReturnType<typeof setTimeout>;
        const refresh = () => {
            const next = localToday();
            setToday(previous => previous.day === next.day && previous.timezone === next.timezone ? previous : next);
        };
        // Align to the minute so an open panel rolls over at local midnight, including DST.
        const tick = () => { refresh(); timer = setTimeout(tick, 60_000 - Date.now() % 60_000); };
        tick();
        window.addEventListener("focus", refresh);
        document.addEventListener("visibilitychange", refresh);
        return () => { clearTimeout(timer); window.removeEventListener("focus", refresh); document.removeEventListener("visibilitychange", refresh); };
    }, []);
    return today;
}

function detailText(event: TimelineEvent) {
    if (event.kind !== "project_context" || !event.detail) return event.detail;
    try {
        const fields: unknown = JSON.parse(event.detail);
        return Array.isArray(fields) && fields.every(field => typeof field === "string") ? fields.map(projectContextLabel).join(", ") : null;
    } catch { return null; }
}

/** Current project history from the API, independent of any generated answer. */
export function ChatHistoryContext({ projectId, projectKey, onOpenFull }: { projectId: number; projectKey: string; onOpenFull?: () => void }) {
    const { day, timezone } = useToday();
    const results = useInfiniteQuery({
        queryKey: [...queryKeys.project(projectId), "chat-today-history", day, timezone],
        initialPageParam: 0,
        queryFn: async ({ pageParam, signal }) => {
            const query = new URLSearchParams({ since: day, until: day, timezone, limit: "20", offset: String(pageParam) });
            const page = await api.get<HistoryPage>(`/api/v1/projects/${projectId}/history?${query}`);
            signal.throwIfAborted();
            if (page.next_offset !== null && page.next_offset <= pageParam) throw new Error("Не удалось продолжить загрузку истории.");
            return page;
        },
        getNextPageParam: page => page.next_offset ?? undefined,
        staleTime: 0,
        refetchOnMount: "always",
        refetchOnWindowFocus: "always",
        retry: false,
    });
    // Never expose a cached page before a live read, or after an access/refetch failure.
    const events = results.error || !results.isFetchedAfterMount ? []
        : [...new Map(results.data?.pages.flatMap(page => page.items).map(event => [event.id, event]) ?? []).values()]
            .sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id));
    const loading = !results.error && (results.isPending || !results.isFetchedAfterMount);
    const historyQuery = new URLSearchParams({ view: "history", since: day, until: day, timezone });
    const formatTime = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: timezone });
    const accessError = results.error instanceof ApiError && [401, 403, 404].includes(results.error.status);

    return <section aria-label="История проекта за сегодня" className="min-w-0 space-y-3">
        <header>
            <h3 className="text-sm font-semibold text-primary">Сегодня в проекте</h3>
            <p className="mt-1 text-[12px] text-muted">{day.split("-").reverse().join(".")} · {timezone}</p>
            <p className="mt-2 text-[12px] leading-relaxed text-secondary">Текущая история проекта. Последние события сверху.</p>
        </header>
        {loading && <div role="status" aria-label="Загрузка истории за сегодня"><Skeleton className="h-24 w-full" /></div>}
        {results.error && <ErrorMessage title="Не удалось загрузить историю" message={accessError ? "Проект недоступен или у вас нет доступа к истории." : "Не удалось получить события. Повторите загрузку."}
            action={<Button size="sm" disabled={results.isFetching} onClick={() => void results.refetch()}>Повторить загрузку</Button>} />}
        {!loading && !results.error && events.length === 0 && <p className="py-3 text-[13px] text-muted">Сегодня событий пока нет.</p>}
        {events.length > 0 && <ol aria-label="События за сегодня" className="divide-y divide-line-subtle">{events.map(event => {
            const structured = event.from_value != null || event.to_value != null;
            const detail = detailText(event);
            return <li key={event.id} className="min-w-0 space-y-2 py-3">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted">
                    <time dateTime={event.created_at} className="font-mono">{formatTime.format(new Date(event.created_at))}</time>
                    {event.actor_name ? <UserIdentity userId={event.actor_user_id} name={event.actor_name} size="xs" /> : <span>Автор не указан</span>}
                </div>
                <p className="text-[13px] font-medium text-primary">{event.title}</p>
                {structured && <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 gap-y-1 text-[12px]">
                    <dt className="text-muted">Было</dt><dd className="whitespace-pre-wrap text-secondary">{historyValue(event.from_value, event.event_type)}</dd>
                    <dt className="text-muted">Стало</dt><dd className="whitespace-pre-wrap text-secondary">{historyValue(event.to_value, event.event_type)}</dd>
                </dl>}
                {detail && <details className="text-[12px] text-secondary"><summary className="touch-target cursor-pointer py-2">Подробности</summary><p className="whitespace-pre-wrap">{detail}</p></details>}
                <div className="flex flex-wrap gap-1">
                    {event.task_id !== null && <Button size="sm" variant="ghost" onClick={() => {
                        onOpenFull?.();
                        const ui = useUiStore.getState();
                        if (event.report_id) ui.openTaskTarget(event.task_id!, { section: "reports", reportId: event.report_id, reportRevision: event.report_revision ?? undefined });
                        else if (event.comment_id) ui.openTaskTarget(event.task_id!, { section: "comments", commentId: event.comment_id });
                        else ui.setSelectedTaskId(event.task_id);
                    }}>{event.report_id ? "Открыть версию отчёта" : event.comment_id ? "Открыть комментарий" : "Открыть задачу"}</Button>}
                    {event.document_ref && <Link onClick={onOpenFull} className="touch-target inline-flex items-center px-2 text-[13px] text-accent hover:underline" to={`/projects/${projectKey}/docs/${encodeURIComponent(event.document_ref)}`}>Открыть документ</Link>}
                </div>
            </li>;
        })}</ol>}
        {!results.error && results.hasNextPage && <Button size="sm" disabled={results.isFetching} onClick={() => void results.fetchNextPage()}>{results.isFetchingNextPage ? "Загрузка…" : "Загрузить более ранние события"}</Button>}
        <Link onClick={onOpenFull} className="touch-target inline-flex items-center text-[13px] text-accent hover:underline" to={`/projects/${projectKey}/knowledge?${historyQuery}`}>Вся история за этот день</Link>
    </section>;
}
