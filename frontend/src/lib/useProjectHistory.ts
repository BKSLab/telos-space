import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useQuery, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { api, ApiError, queryKeys } from "./api";

export type ProjectHistoryKind = "task_activity" | "task_comment" | "task_report" | "project_deadline" | "project_context"
    | "risk_created" | "document_created" | "project_activity" | "project_member" | "project_stage"
    | "risk_activity" | "document_activity" | "project_plan" | "project_board";
export interface TimelineEvent {
    id: string; kind: ProjectHistoryKind; title: string; detail: string | null; created_at: string;
    actor_name: string | null; actor_user_id?: number | null; task_id: number | null;
    document_ref?: string | null; from_value?: string | null; to_value?: string | null;
    comment_id?: number | null; report_id?: number | null; report_revision?: number | null; event_type?: string | null;
    source_available?: boolean;
}
interface TimelinePage { items: TimelineEvent[]; next_offset: number | null; previous_day: string | null; revision: string }
export interface HistoryWeek { items: TimelineEvent[]; since: string; until: string; nextUntil: string | null; revision?: string }
function denied(error: unknown): error is ApiError { return error instanceof ApiError && [401, 403, 404].includes(error.status); }

export function shiftHistoryDay(day: string, count: number) {
    const date = new Date(`${day}T12:00:00Z`);
    date.setUTCDate(date.getUTCDate() + count);
    return date.toISOString().slice(0, 10);
}

/** Complete a calendar window before publishing it, so day counts never describe a partial page. */
export async function readHistoryWeek(projectId: number, filters: Record<string, string>, timezone: string, until: string, eventId: string | null, signal: AbortSignal): Promise<HistoryWeek> {
    const weekStart = until < "0001-01-07" ? "0001-01-01" : shiftHistoryDay(until, -6);
    const since = filters.since && filters.since > weekStart ? filters.since : weekStart;
    const includePrevious = !eventId && (!filters.since || since > filters.since) && since > "0001-01-01";
    const base = new URLSearchParams({ timezone });
    if (eventId) base.set("event_id", eventId);
    else for (const [key, value] of Object.entries(filters)) if (value && key !== "since" && key !== "until") base.set(key, value);
    const read = async (query: URLSearchParams) => {
        signal.throwIfAborted();
        const result = await api.get<TimelinePage>(`/api/v1/projects/${projectId}/history?${query}`, { signal });
        signal.throwIfAborted();
        return result;
    };
    const items = new Map<string, TimelineEvent>();
    let nextUntil: string | null = null;
    let revision = "0";
    let offset = 0;
    for (;;) {
        const query = new URLSearchParams(base);
        query.set("limit", "100"); query.set("offset", String(offset));
        if (!eventId) { query.set("since", since); query.set("until", until); }
        if (includePrevious && offset === 0) query.set("include_previous", "true");
        const page = await read(query);
        if (offset === 0) revision = page.revision ?? "0";
        if (offset === 0 && includePrevious && page.previous_day && page.previous_day < since && (!filters.since || page.previous_day >= filters.since)) nextUntil = page.previous_day;
        for (const item of page.items) items.set(item.id, item);
        if (page.next_offset === null || eventId) break;
        if (page.next_offset <= offset) throw new Error("Не удалось продолжить загрузку истории. Повторите попытку.");
        offset = page.next_offset;
    }
    return { items: [...items.values()], since, until, nextUntil, revision };
}

export function useProjectHistory(projectId: number, filters: Record<string, string>, timezone: string, today: string, eventId: string | null, visibleDays: string[] = []) {
    const client = useQueryClient();
    const filterKey = JSON.stringify(filters);
    const stableFilters = useMemo(() => JSON.parse(filterKey) as Record<string, string>, [filterKey]);
    const queryKey = useMemo(() => [...queryKeys.project(projectId), "history-weeks", stableFilters, timezone, today, eventId], [projectId, stableFilters, timezone, today, eventId]);
    const key = JSON.stringify(queryKey);
    const pending = useRef(new Map<string, AbortController>());
    const failed = useRef(new Set<string>());
    const emptyAttempt = useRef("");
    const [refreshState, setRefreshState] = useState<{ key: string; fetching: number; error: Error | null }>({ key, fetching: 0, error: null });
    const clearDenied = useCallback(async (except?: string) => {
        for (const controller of pending.current.values()) controller.abort();
        pending.current.clear();
        // A denial applies across every filter and event view of this project's history.
        await client.cancelQueries({ queryKey: [...queryKeys.project(projectId), "history-weeks"], predicate: query => JSON.stringify(query.queryKey) !== except });
        client.setQueriesData<InfiniteData<HistoryWeek, string>>({ queryKey: [...queryKeys.project(projectId), "history-weeks"] }, cached => cached ? { pages: [], pageParams: [] } : undefined, { updatedAt: 0 });
    }, [client, projectId]);
    const version = useQuery({
        queryKey: [...queryKeys.project(projectId), "history-version"],
        queryFn: async ({ signal }) => {
            try { return await api.get<{ revision: string }>(`/api/v1/projects/${projectId}/history/version`, { signal }); }
            catch (error) { if (denied(error)) await clearDenied(); throw error; }
        },
        staleTime: 0,
        refetchInterval: 30_000,
        refetchOnWindowFocus: "always",
        refetchOnReconnect: "always",
        retry: false,
    });
    const revision = version.data?.revision;
    const results = useInfiniteQuery({
        queryKey,
        initialPageParam: stableFilters.until || (stableFilters.since && stableFilters.since > today ? stableFilters.since : today),
        queryFn: async ({ pageParam, signal }) => {
            try {
                const loaded = await readHistoryWeek(projectId, stableFilters, timezone, pageParam, eventId, signal);
                setRefreshState(state => state.key === key ? { ...state, error: null } : state);
                return loaded;
            } catch (error) {
                if (denied(error)) await clearDenied(key);
                throw error;
            }
        },
        getNextPageParam: last => last.nextUntil ?? undefined,
        enabled: !denied(version.error),
        // Version reads validate freshness; automatic infinite refetch would reload every archive week.
        staleTime: "static",
        gcTime: 10 * 60_000,
        retry: false,
    });
    const visibleKey = visibleDays.join(",");
    const refreshWindows = useCallback(async (currentRevision: string, attempt: string, force = false) => {
        const cached = client.getQueryData<InfiniteData<HistoryWeek, string>>(queryKey);
        if (!cached?.pages.length) return;
        const days = visibleKey ? visibleKey.split(",") : [];
        const visible = cached.pages.filter(week => days.some(day => day >= week.since && day <= week.until));
        const targets = visible.length ? visible : cached.pages.slice(0, 1);
        await Promise.all(targets.map(async week => {
            const requestKey = `${key}:${week.until}:${currentRevision}`;
            const attemptKey = `${requestKey}:${attempt}`;
            const current = week.revision != null && BigInt(week.revision) >= BigInt(currentRevision);
            if (pending.current.has(requestKey) || (!force && (current || failed.current.has(attemptKey)))) return;
            const controller = new AbortController();
            pending.current.set(requestKey, controller);
            setRefreshState({ key, fetching: pending.current.size, error: null });
            try {
                const updated = await readHistoryWeek(projectId, stableFilters, timezone, week.until, eventId, controller.signal);
                controller.signal.throwIfAborted();
                client.setQueryData<InfiniteData<HistoryWeek, string>>(queryKey, data => data && {
                    ...data, pages: data.pages.map(page => page.until === week.until && page.since === week.since ? updated : page),
                });
            } catch (error) {
                if (controller.signal.aborted) return;
                failed.current.add(attemptKey);
                if (denied(error)) await clearDenied();
                setRefreshState({ key, fetching: pending.current.size, error: error instanceof Error ? error : new Error("Не удалось обновить историю.") });
            } finally {
                pending.current.delete(requestKey);
                setRefreshState(state => state.key === key ? { ...state, fetching: pending.current.size } : state);
            }
        }));
    }, [client, queryKey, key, visibleKey, projectId, stableFilters, timezone, eventId, clearDenied]);
    useEffect(() => {
        const requests = pending.current;
        const failures = failed.current;
        return () => { for (const controller of requests.values()) controller.abort(); requests.clear(); failures.clear(); };
    }, [key, revision]);
    const { isFetching: fetchingWindow, data: weekData, dataUpdatedAt, refetch: refetchAll } = results;
    useEffect(() => {
        if (!revision || version.error || fetchingWindow || !weekData) return;
        const attempt = `${key}:${version.dataUpdatedAt}`;
        if (weekData.pages.length) {
            emptyAttempt.current = attempt;
            void refreshWindows(revision, String(version.dataUpdatedAt));
        } else if (emptyAttempt.current !== attempt) {
            emptyAttempt.current = attempt;
            void refetchAll();
        }
    }, [key, revision, version.error, version.dataUpdatedAt, fetchingWindow, dataUpdatedAt, weekData, refetchAll, refreshWindows]);
    const refetch = async () => {
        const currentVersion = await version.refetch();
        if (currentVersion.error || !currentVersion.data) return;
        setRefreshState({ key, fetching: 0, error: null });
        if (results.error || !client.getQueryData<InfiniteData<HistoryWeek, string>>(queryKey)?.pages.length) await results.refetch();
        else await refreshWindows(currentVersion.data.revision, String(currentVersion.dataUpdatedAt), true);
    };
    const background = refreshState.key === key ? refreshState : undefined;
    const externalError = version.error ?? background?.error;
    const error = externalError ?? results.error;
    return {
        ...results,
        refetch,
        error,
        isError: !!error,
        isSuccess: results.isSuccess && !error,
        isPending: results.isPending && !error,
        isFetching: results.isFetching || version.isFetching || !!background?.fetching,
        isRefetching: results.isRefetching || !!background?.fetching,
        isFetchNextPageError: !externalError && results.isFetchNextPageError,
    };
}
