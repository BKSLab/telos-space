import { apiUrl } from "@/lib/api";

export interface TaskViewToken { revision: number; deadline_revision: number; overdue: boolean }
export interface TaskAttentionItem extends TaskViewToken { task_id: number; is_new: boolean; is_overdue: boolean; is_updated: boolean }
export interface TaskAttentionSnapshot { new_tasks: number; overdue_tasks: number; updated_tasks: number; total: number; items: TaskAttentionItem[] }
export type AttentionStatus = "connecting" | "connected" | "disconnected" | "forbidden";
export const attentionEndpoint = "/api/v1/users/me/task-attention";
export const attentionKey = (userId: number) => ["task-attention", userId] as const;

export function attentionTitle(data: TaskAttentionSnapshot): string {
    return `Требуют просмотра: ${data.total}. Новые: ${data.new_tasks}; нарушение срока: ${data.overdue_tasks}; комментарии и изменения: ${data.updated_tasks}`;
}

export function attentionBadges(data: TaskAttentionSnapshot | undefined) {
    return data ? { tasks: data.total, tasks_new: data.new_tasks, tasks_overdue: data.overdue_tasks, tasks_updated: data.updated_tasks } : {};
}

export function isAttentionSnapshot(value: unknown): value is TaskAttentionSnapshot {
    if (!value || typeof value !== "object") return false;
    const data = value as TaskAttentionSnapshot;
    const count = (n: unknown) => Number.isSafeInteger(n) && Number(n) >= 0;
    return [data.new_tasks, data.overdue_tasks, data.updated_tasks, data.total].every(count)
        && Array.isArray(data.items) && data.items.every(item => item && count(item.task_id) && item.task_id > 0
            && count(item.revision) && item.revision > 0 && count(item.deadline_revision) && item.deadline_revision > 0
            && [item.is_new, item.is_overdue, item.is_updated, item.overdue].every(flag => typeof flag === "boolean"));
}

/** Один транспорт; полный снимок на reconnect, heartbeat и ограниченный backoff. */
export function connectTaskAttention(options: {
    onSnapshot: (data: TaskAttentionSnapshot) => void;
    onStatus: (status: AttentionStatus) => void;
    createSocket?: (url: string) => WebSocket;
}) {
    let stopped = false;
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let watchdog: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    const alive = () => {
        clearTimeout(watchdog);
        watchdog = setTimeout(() => socket?.close(), 75_000);
    };
    const connect = () => {
        if (stopped) return;
        options.onStatus("connecting");
        const url = new URL(apiUrl(attentionEndpoint + "/ws"), window.location.origin);
        url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
        socket = (options.createSocket ?? ((address) => new WebSocket(address)))(url.toString());
        socket.onmessage = event => {
            alive();
            let message;
            try { message = JSON.parse(event.data); } catch { return; }
            if (message.type === "ping") socket?.send(JSON.stringify({ type: "pong" }));
            if (message.type === "task-attention" && isAttentionSnapshot(message.data)) {
                attempt = 0;
                options.onSnapshot(message.data);
                options.onStatus("connected");
            }
        };
        socket.onopen = alive;
        socket.onerror = () => socket?.close();
        socket.onclose = event => {
            clearTimeout(watchdog);
            if (stopped) return;
            if (event.code === 4403) { options.onStatus("forbidden"); return; }
            options.onStatus("disconnected");
            retry = setTimeout(connect, Math.min(30_000, 1000 * 2 ** Math.min(attempt++, 5)));
        };
    };
    connect();
    return () => {
        stopped = true;
        clearTimeout(retry);
        clearTimeout(watchdog);
        socket?.close();
    };
}
