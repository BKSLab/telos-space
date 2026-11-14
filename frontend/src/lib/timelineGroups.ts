import { buildTimelineWbsRows } from "@/lib/timelineWbs";
import type { CalendarStage, CalendarTask, CalendarWbsNode, UserSummary } from "@/lib/types";

export interface TimelineAggregate {
    total: number;
    done: number;
    risks: number;
    progress: number;
    startDate: string | null;
    dueDate: string | null;
}

/** Чем группируются строки шкалы проекта: стадиями или исполнителями. */
export type TimelineGroupBy = "stage" | "assignee" | "wbs";
export const TIMELINE_GROUP_BY: readonly TimelineGroupBy[] = ["wbs", "stage", "assignee"];

/** Строка-группа шкалы времени: стадия проекта или исполнитель со сводкой по задачам. */
export interface TimelineGroupRow {
    id: string;
    kind: "group";
    stageId: number | null;
    depth?: number;
    groupKey?: TimelineGroupKey;
    /** Группировка по исполнителям: подпись исполнителя, null — «Без исполнителя». */
    assignee?: string | null;
    user?: UserSummary | null;
    title: string;
    color: string | null;
    collapsed: boolean;
    aggregate: TimelineAggregate;
}

export interface TimelineTaskRow {
    id: string;
    kind: "task";
    depth?: number;
    task: CalendarTask;
}

export type TimelineRow = TimelineGroupRow | TimelineTaskRow;

/**
 * Ключ свёрнутой группы; задачи неизвестной стадии собираются в «other»,
 * группы исполнителей — «assignee:<подпись>», без исполнителя — «assignee:».
 */
export type TimelineGroupKey = number | string;

export function aggregateTimeline(tasks: CalendarTask[]): TimelineAggregate {
    const scheduled = tasks.filter((task) => task.start_date !== null || task.due_date !== null);
    const starts = scheduled.map((task) => task.start_date ?? (task.due_date as string));
    const ends = scheduled.map((task) => task.due_date ?? (task.start_date as string));
    const done = tasks.filter((task) => task.is_done).length;
    return {
        total: tasks.length,
        done,
        risks: tasks.filter((task) => task.risk_level !== null && !task.is_done).length,
        progress: tasks.length === 0 ? 0 : Math.round((done / tasks.length) * 100),
        startDate: starts.length === 0 ? null : starts.sort()[0],
        dueDate: ends.length === 0 ? null : (ends.sort().at(-1) ?? null),
    };
}

function byDates(first: CalendarTask, second: CalendarTask): number {
    return (
        (first.start_date ?? first.due_date ?? "").localeCompare(second.start_date ?? second.due_date ?? "") ||
        first.key.localeCompare(second.key)
    );
}

/**
 * Строки шкалы времени, сгруппированные по стадиям проекта в порядке доски.
 * Пустые стадии не показываются: на шкале они только занимают место.
 */
export function buildTimelineStageRows(
    stages: CalendarStage[],
    tasks: CalendarTask[],
    collapsed: ReadonlySet<TimelineGroupKey>,
): TimelineRow[] {
    const ordered = [...stages].sort((first, second) => first.order_index - second.order_index || first.id - second.id);
    const known = new Set(ordered.map((stage) => stage.id));
    const buckets = new Map<TimelineGroupKey, CalendarTask[]>();
    for (const task of tasks) {
        const key: TimelineGroupKey = known.has(task.stage_id) ? task.stage_id : "other";
        const bucket = buckets.get(key) ?? [];
        bucket.push(task);
        buckets.set(key, bucket);
    }

    const rows: TimelineRow[] = [];
    const push = (key: TimelineGroupKey, title: string, color: string | null) => {
        const items = buckets.get(key);
        if (!items?.length) return;
        items.sort(byDates);
        const isCollapsed = collapsed.has(key);
        rows.push({
            id: `group:${key}`,
            kind: "group",
            stageId: typeof key === "number" ? key : null,
            title,
            color,
            collapsed: isCollapsed,
            aggregate: aggregateTimeline(items),
        });
        if (!isCollapsed) for (const task of items) rows.push({ id: `task:${task.id}`, kind: "task", task });
    };
    for (const stage of ordered) push(stage.id, stage.name, stage.color);
    push("other", "Без стадии", null);
    return rows;
}

/** Ключ группы исполнителя для свёртки. */
export function assigneeGroupKey(assignee: string | null): TimelineGroupKey {
    return `assignee:${assignee ?? ""}`;
}

/**
 * Строки шкалы, сгруппированные по исполнителям: по алфавиту, «Без исполнителя» — в конце.
 * ``people`` — участники из ответа загрузки: у них строка есть, даже если задач проекта
 * на шкале нет (видно, кто свободен или занят в других проектах).
 */
export function buildTimelineAssigneeRows(
    tasks: CalendarTask[],
    collapsed: ReadonlySet<TimelineGroupKey>,
    people: readonly string[] = [],
): TimelineRow[] {
    const buckets = new Map<string, CalendarTask[]>();
    for (const name of people) if (name) buckets.set(name, []);
    const unassigned: CalendarTask[] = [];
    for (const task of tasks) {
        const name = task.assignee?.trim();
        if (!name) {
            unassigned.push(task);
            continue;
        }
        const bucket = buckets.get(name) ?? [];
        bucket.push(task);
        buckets.set(name, bucket);
    }
    const rows: TimelineRow[] = [];
    const push = (assignee: string | null, items: CalendarTask[]) => {
        items.sort(byDates);
        const executors = items.flatMap(task => task.executor ? [task.executor] : []);
        const user = executors.length === items.length && new Set(executors.map(person => person.id)).size === 1 ? executors[0] : null;
        const key = assigneeGroupKey(assignee);
        const isCollapsed = collapsed.has(key);
        rows.push({
            id: `group:${key}`,
            kind: "group",
            stageId: null,
            assignee,
            ...(user ? { user } : {}),
            title: assignee ?? "Без исполнителя",
            color: null,
            collapsed: isCollapsed,
            aggregate: aggregateTimeline(items),
        });
        if (!isCollapsed) for (const task of items) rows.push({ id: `task:${task.id}`, kind: "task", task });
    };
    const names = [...buckets.keys()].sort((first, second) => first.localeCompare(second, "ru"));
    for (const name of names) push(name, buckets.get(name) ?? []);
    if (unassigned.length > 0) push(null, unassigned);
    return rows;
}

export function buildTimelineWbsGroupRows(nodes: CalendarWbsNode[], tasks: CalendarTask[], collapsed: ReadonlySet<TimelineGroupKey>): TimelineRow[] {
    const wbsCollapsed = new Set<number | "unassigned">();
    for (const node of nodes) if (collapsed.has(`wbs:${node.id}`)) wbsCollapsed.add(node.id);
    if (collapsed.has("wbs:unassigned")) wbsCollapsed.add("unassigned");
    return buildTimelineWbsRows(nodes, tasks, wbsCollapsed).map(row => row.kind === "task" ? row : ({
        id: row.id, kind: "group" as const, stageId: null, depth: row.depth,
        groupKey: `wbs:${row.nodeId ?? "unassigned"}`, title: `${row.number ? row.number + " " : ""}${row.title}`,
        color: null, collapsed: row.collapsed, aggregate: row.aggregate,
    }));
}
