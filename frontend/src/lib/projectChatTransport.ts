import { ApiError } from "@/lib/api";
import type { ChatAck, ChatConnectionState, ChatEvent, ChatEventPage } from "@/lib/projectChat";
import { newUuid } from "@/lib/uuid";

interface TransportOptions {
    url: string;
    cursor: number;
    fetchEvents: (after: number) => Promise<ChatEventPage>;
    onEvent: (event: ChatEvent, replay: boolean) => void;
    onState: (state: ChatConnectionState) => void;
    onReady: (data: { presence?: number[]; typing?: number[]; can_write: boolean }) => void;
    createSocket?: (url: string) => WebSocket;
}

/** Подписка и resync событий; ни история, ни typing не опрашиваются таймером GET. */
export class ProjectChatTransport {
    private socket: WebSocket | null = null;
    private cursor: number;
    private stopped = false;
    private attempts = 0;
    private reconnectTimer?: ReturnType<typeof setTimeout>;
    private heartbeatTimer?: ReturnType<typeof setInterval>;
    private pending = new Map<string, { resolve: (ack: ChatAck) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> }>();
    private buffered = new Map<number, ChatEvent>();
    private syncing: Promise<void> | null = null;
    private ready = false;
    private lastReceivedAt = 0;
    private options: TransportOptions;

    constructor(options: TransportOptions) { this.options = options; this.cursor = options.cursor; }

    start() { this.stopped = false; this.connect(); }

    stop() {
        this.stopped = true;
        this.ready = false;
        clearTimeout(this.reconnectTimer);
        clearInterval(this.heartbeatTimer);
        this.socket?.close();
        this.rejectPending();
    }

    command(type: string, data: object = {}, messageId?: number): Promise<ChatAck> {
        if (!this.ready || this.socket?.readyState !== WebSocket.OPEN) return Promise.reject(new Error("Нет связи с чатом. Черновик сохранён."));
        const requestId = newUuid();
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => {
                this.pending.delete(requestId);
                reject(new Error("Подтверждение не получено. Можно безопасно повторить отправку."));
            }, 15000);
            this.pending.set(requestId, { resolve, reject, timer });
            this.socket!.send(JSON.stringify({ type, request_id: requestId, data, ...(messageId ? { message_id: messageId } : {}) }));
        });
    }

    ephemeral(type: "typing.started" | "typing.stopped") {
        if (this.ready && this.socket?.readyState === WebSocket.OPEN) {
            this.socket.send(JSON.stringify({ type, request_id: newUuid(), data: {} }));
        }
    }

    private connect() {
        if (this.stopped) return;
        this.options.onState(this.attempts ? "reconnecting" : "connecting");
        const socket = (this.options.createSocket ?? ((url) => new WebSocket(url)))(this.options.url);
        this.socket = socket;
        socket.onmessage = (message) => {
            if (this.stopped || this.socket !== socket) return;
            let event;
            try { event = JSON.parse(String(message.data)); } catch { return; }
            this.lastReceivedAt = Date.now();
            if (event.type === "ready") {
                this.options.onReady(event.data);
                clearInterval(this.heartbeatTimer);
                const heartbeat = (event.data.heartbeat_seconds ?? 20) * 1000;
                this.heartbeatTimer = setInterval(() => {
                    if (Date.now() - this.lastReceivedAt >= heartbeat * 3) { socket.close(); return; }
                    if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "ping", request_id: newUuid(), data: {} }));
                }, heartbeat);
                this.options.onState("syncing");
                void this.sync().then(() => {
                    if (this.stopped || this.socket !== socket || socket.readyState !== WebSocket.OPEN) return;
                    this.ready = true;
                    this.attempts = 0;
                    this.options.onState("connected");
                }).catch(() => socket.close());
            } else if (event.type === "ack" || event.type === "error") {
                const request = this.pending.get(event.request_id);
                if (!request) return;
                clearTimeout(request.timer);
                this.pending.delete(event.request_id);
                if (event.type === "error") request.reject(new ApiError(event.status, event.detail));
                else request.resolve(event.data);
            } else if (typeof event.seq === "number") {
                if (event.seq <= this.cursor) return;
                this.buffered.set(event.seq, event);
                if (!this.syncing) {
                    this.drain();
                    if (this.buffered.size) void this.sync().catch(() => socket.close());
                }
            } else {
                this.options.onEvent(event, false);
            }
        };
        socket.onerror = () => socket.close();
        socket.onclose = (event) => {
            if (this.socket !== socket) return;
            this.ready = false;
            clearInterval(this.heartbeatTimer);
            this.rejectPending();
            if (this.stopped) return;
            if (event.code === 4401 || event.code === 4403) {
                this.stopped = true;
                this.options.onState("forbidden");
                return;
            }
            this.options.onState("reconnecting");
            const delay = Math.min(15000, 500 * 2 ** Math.min(this.attempts++, 5)) + Math.random() * 300;
            this.reconnectTimer = setTimeout(async () => {
                // При отказе handshake браузер часто видит только 1006, поэтому проверяем
                // доступ HTTP-запросом resync перед следующей попыткой подключения.
                try { await this.options.fetchEvents(this.cursor); }
                catch (error) {
                    if (error instanceof ApiError && [401, 403, 404].includes(error.status)) {
                        this.stopped = true; this.options.onState("forbidden"); return;
                    }
                }
                this.connect();
            }, delay);
        };
    }

    private sync(): Promise<void> {
        if (this.syncing) return this.syncing;
        this.syncing = (async () => {
            let more = true;
            while (more && !this.stopped) {
                const page = await this.options.fetchEvents(this.cursor);
                if (this.stopped) return;
                for (const event of page.events) {
                    if (event.seq === undefined || event.seq <= this.cursor) continue;
                    this.options.onEvent(event, true);
                    this.cursor = event.seq;
                    this.buffered.delete(event.seq);
                }
                more = page.has_more;
            }
            this.drain();
        })().finally(() => { this.syncing = null; });
        return this.syncing;
    }

    private drain() {
        for (const seq of this.buffered.keys()) if (seq <= this.cursor) this.buffered.delete(seq);
        while (this.buffered.has(this.cursor + 1)) {
            const event = this.buffered.get(this.cursor + 1)!;
            this.buffered.delete(this.cursor + 1);
            this.options.onEvent(event, false);
            this.cursor += 1;
        }
    }

    private rejectPending() {
        for (const request of this.pending.values()) {
            clearTimeout(request.timer);
            request.reject(new Error("Соединение прервано. Повторная отправка не создаст дубль."));
        }
        this.pending.clear();
    }
}
