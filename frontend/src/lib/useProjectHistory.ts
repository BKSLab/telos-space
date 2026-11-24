import { useInfiniteQuery, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { api, ApiError, queryKeys } from "./api";

export interface TimelineEvent {
    id: string; kind: string; title: string; detail: string | null; created_at: string;
    actor_name: string | null; actor_user_id?: number | null; task_id: number | null;
    document_ref?: string | null; from_value?: string | null; to_value?: string | null;
    comment_id?: number | null; report_id?: number | null; report_revision?: number | null; event_type?: string | null;
}
interface TimelinePage { items: TimelineEvent[]; next_offset: number | null; previous_day: string | null }
export interface HistoryWeek { items: TimelineEvent[]; since: string; until: string; nextUntil: string | null }

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
    let offset = 0;
    for (;;) {
        const query = new URLSearchParams(base);
        query.set("limit", "100"); query.set("offset", String(offset));
        if (!eventId) { query.set("since", since); query.set("until", until); }
        if (includePrevious && offset === 0) query.set("include_previous", "true");
        const page = await read(query);
        if (offset === 0 && includePrevious && page.previous_day && page.previous_day < since && (!filters.since || page.previous_day >= filters.since)) nextUntil = page.previous_day;
        for (const item of page.items) items.set(item.id, item);
        if (page.next_offset === null || eventId) break;
        if (page.next_offset <= offset) throw new Error("Не удалось продолжить загрузку истории. Повторите попытку.");
        offset = page.next_offset;
    }
    return { items: [...items.values()], since, until, nextUntil };
}

export function useProjectHistory(projectId: number, filters: Record<string, string>, timezone: string, today: string, eventId: string | null) {
    const client = useQueryClient();
    const queryKey = [...queryKeys.project(projectId), "history-weeks", filters, timezone, today, eventId];
    return useInfiniteQuery({
        queryKey,
        initialPageParam: filters.until || (filters.since && filters.since > today ? filters.since : today),
        queryFn: async ({ pageParam, signal }) => {
            try {
                return await readHistoryWeek(projectId, filters, timezone, pageParam, eventId, signal);
            } catch (error) {
                if (error instanceof ApiError && [401, 403, 404].includes(error.status)) {
                    // A later network error or remount must not resurrect denied event contents.
                    client.setQueryData<InfiniteData<HistoryWeek, string>>(queryKey, cached => cached ? { pages: [], pageParams: [] } : undefined, { updatedAt: 0 });
                }
                throw error;
            }
        },
        getNextPageParam: last => last.nextUntil ?? undefined,
        // Past events can change after edits, transfers and access updates too.
        staleTime: 60_000,
        gcTime: 10 * 60_000,
        retry: false,
    });
}
