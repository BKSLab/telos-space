import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { MessageSquare, X } from "lucide-react";
import { api, apiUrl, ApiError, queryKeys } from "@/lib/api";
import { useCurrentUser } from "@/lib/useAuth";
import { ProjectChatContext, type ProjectChatContextValue } from "@/lib/useProjectChat";
import { applyChatMessage, chatAuthorName, chatEntityKey, chatKeys, chatPath, isAgentThinking, isTaskMove, taskMoveText, type ChatAck, type ChatConnectionState, type ChatEntity, type ChatEntityRef, type ChatEvent, type ChatEventPage, type ChatHistory, type ChatMessage, type ChatMessagePage, type ChatTaskMoveNotice, type ProjectChat } from "@/lib/projectChat";
import { ProjectChatTransport } from "@/lib/projectChatTransport";
import { IconButton } from "@/components/ui/Button";

/** Живёт вместе с проектом: вкладки не разрывают сокет и не теряют счётчик. */
export function ProjectChatProvider({ projectId, projectKey, children, chatVisible = false }: { projectId: number; projectKey: string; children: ReactNode; chatVisible?: boolean }) {
    const user = useCurrentUser().data;
    const userId = user?.id ?? 0;
    const client = useQueryClient();
    const navigate = useNavigate();
    const info = useQuery({ queryKey: chatKeys.info(projectId, userId), queryFn: () => api.get<ProjectChat>(chatPath(projectId)), enabled: userId > 0 && projectId > 0, staleTime: Infinity, retry: false, refetchOnWindowFocus: false });
    const infoRef = useRef(info.data);
    const visible = useRef(chatVisible);
    const transport = useRef<ProjectChatTransport | null>(null);
    const [state, setState] = useState<ChatConnectionState>("connecting");
    const [online, setOnline] = useState<number[]>([]);
    const [typing, setTyping] = useState<number[]>([]);
    const [canWrite, setCanWrite] = useState(true);
    const [notification, setNotification] = useState<ChatMessage | null>(null);
    const [moveNotices, setMoveNotices] = useState<{ projectId: number; userId: number; items: ChatTaskMoveNotice[] }>({ projectId, userId, items: [] });
    const taskMoves = useMemo(() => moveNotices.projectId === projectId && moveNotices.userId === userId ? moveNotices.items : [], [moveNotices, projectId, userId]);
    const chatId = info.data?.id;

    useEffect(() => { infoRef.current = info.data; }, [info.data]);
    useLayoutEffect(() => { visible.current = chatVisible; }, [chatVisible]);
    useEffect(() => {
        if (!notification) return;
        const timer = setTimeout(() => setNotification(null), 8000);
        return () => clearTimeout(timer);
    }, [notification]);

    useEffect(() => {
        if (!chatId || !userId) return;
        let alive = true;
        let infoTimer: ReturnType<typeof setTimeout> | undefined;
        let entityTimer: ReturnType<typeof setTimeout> | undefined;
        let searchChanged = false;
        const changedEntities = new Map<string, ChatEntityRef>();
        const collectVisibleEntities = () => {
            for (const [, history] of client.getQueriesData<ChatHistory>({ queryKey: chatKeys.messages(projectId, userId) })) {
                for (const message of history?.messages ?? []) for (const ref of message.entities) changedEntities.set(chatEntityKey(ref), ref.entity_type === "DOCUMENT" ? { entity_type: "DOCUMENT", entity_id: null, document_ref: ref.document_ref } : { entity_type: ref.entity_type, entity_id: ref.entity_id });
            }
        };
        const refreshInfo = () => {
            clearTimeout(infoTimer);
            infoTimer = setTimeout(() => {
                void client.invalidateQueries({ queryKey: chatKeys.info(projectId, userId) });
                if (searchChanged) { searchChanged = false; void client.invalidateQueries({ queryKey: chatKeys.search(projectId, userId) }); }
            }, 180);
        };
        const refreshEntities = () => {
            clearTimeout(entityTimer);
            entityTimer = setTimeout(async () => {
                const refs = [...changedEntities.values()].slice(0, 100);
                for (const ref of refs) changedEntities.delete(chatEntityKey(ref));
                if (!refs.length) return;
                try {
                    const values = await api.post<ChatEntity[]>(`${chatPath(projectId)}/entities/resolve`, refs);
                    if (!alive) return;
                    const wanted = new Set(refs.map(chatEntityKey));
                    const updates = new Map(values.map((row) => [chatEntityKey(row), row]));
                    client.setQueriesData<ChatHistory>({ queryKey: chatKeys.messages(projectId, userId) }, (old) => old && ({ ...old, messages: old.messages.map((message) => ({ ...message, entities: message.entities.map((ref) => {
                        const key = chatEntityKey(ref);
                        return wanted.has(key) ? updates.get(key) ?? { ...ref, available: false, title: "Объект больше недоступен", href: null, status: null } : ref;
                    }) })) }));
                    void client.invalidateQueries({ queryKey: chatKeys.entities(projectId, userId) });
                    if (changedEntities.size) refreshEntities();
                } catch { /* Следующий resync повторно гидратирует карточки. */ }
            }, 100);
        };
        const onEvent = (event: ChatEvent, replay: boolean) => {
            if (!alive || event.project_id !== projectId) return;
            if (event.type === "presence.updated") { setOnline(event.data.user_ids ?? []); return; }
            if (event.type === "typing.updated") { setTyping(event.data.user_ids ?? []); return; }
            const message = event.data.message;
            if (message) {
                client.setQueriesData<ChatHistory>({ queryKey: chatKeys.messages(projectId, userId) }, (old) => applyChatMessage(old, { ...message, eventSeq: event.seq }));
                if (event.type.startsWith("message.")) {
                    searchChanged = true;
                    client.setQueriesData<InfiniteData<ChatMessagePage>>({ queryKey: chatKeys.search(projectId, userId) }, (old) => old && ({ ...old, pages: old.pages.map((page) => ({ ...page, messages: page.messages.filter((row) => row.id !== message.id || !message.deleted_at).map((row) => row.id === message.id ? message : row) })) }));
                    setNotification((old) => old?.id === message.id ? message.deleted_at ? null : message : old);
                }
                if (!replay && event.type === "message.created" && !message.deleted_at && !isAgentThinking(message) && message.author?.id !== userId && (!visible.current || document.hidden || !document.hasFocus())) setNotification(message);
            }
            if (event.type === "entity.updated" && event.data.entity_type && (event.data.entity_id || event.data.document_ref)) {
                const ref: ChatEntityRef = event.data.entity_type === "DOCUMENT"
                    ? { entity_type: "DOCUMENT", entity_id: null, document_ref: event.data.document_ref }
                    : { entity_type: event.data.entity_type, entity_id: event.data.entity_id! };
                if (ref.entity_type === "TASK" && ref.entity_id !== null) void client.invalidateQueries({ queryKey: queryKeys.task(ref.entity_id) });
                if (ref.entity_type === "DOCUMENT" && ref.document_ref) {
                    void client.invalidateQueries({ queryKey: queryKeys.document(ref.document_ref) });
                    void client.invalidateQueries({ queryKey: ["documents", "text", ref.document_ref] });
                    void client.invalidateQueries({ queryKey: ["documents", "list"] });
                }
                changedEntities.set(chatEntityKey(ref), ref);
                refreshEntities();
            }
            if (event.type === "entities.refresh") { collectVisibleEntities(); refreshEntities(); }
            if (event.type === "task.moved" && isTaskMove(event.data) && Number.isSafeInteger(event.seq) && event.seq! > 0) {
                const move = event.data;
                const notice: ChatTaskMoveNotice = { seq: event.seq!, text: taskMoveText(move, infoRef.current?.members ?? []), taskId: move.task_id, direction: move.direction };
                // Reconnect может повторить событие; в чате остаются последние пять переносов.
                setMoveNotices((old) => ({ projectId, userId, items: [...(old.projectId === projectId && old.userId === userId ? old.items : []).filter((row) => row.seq !== notice.seq), notice].slice(-5) }));
                // Общий префикс проектов включает доски, WBS, календарь и зависимости.
                for (const key of [queryKeys.projects, queryKeys.myTasks, queryKeys.dashboard, queryKeys.task(move.task_id), queryKeys.taskActivity(move.task_id), ["documents"]]) void client.invalidateQueries({ queryKey: key });
                changedEntities.set(`TASK:${move.task_id}`, { entity_type: "TASK", entity_id: move.task_id });
                refreshEntities();
            }
            if (event.type.startsWith("message.") || event.type.startsWith("member.") || event.type === "read.updated") refreshInfo();
        };
        const url = new URL(apiUrl(`${chatPath(projectId)}/ws`), window.location.origin);
        url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
        const socket = new ProjectChatTransport({
            url: url.toString(), cursor: infoRef.current?.event_cursor ?? 0,
            fetchEvents: (after) => api.get<ChatEventPage>(`${chatPath(projectId)}/events?after=${after}`),
            onEvent,
            onState: (value) => {
                if (!alive) return;
                setState(value);
                if (value === "forbidden") {
                    client.removeQueries({ queryKey: chatKeys.messages(projectId, userId) });
                    void client.invalidateQueries({ queryKey: chatKeys.info(projectId, userId) });
                }
                if (value === "connected") { refreshInfo(); collectVisibleEntities(); refreshEntities(); }
            },
            onReady: (value) => { if (alive) { setOnline(value.presence ?? []); setTyping(value.typing ?? []); setCanWrite(value.can_write); } },
        });
        transport.current = socket;
        socket.start();
        return () => { alive = false; clearTimeout(infoTimer); clearTimeout(entityTimer); socket.stop(); transport.current = null; };
    }, [projectId, userId, chatId, client]);

    const command = useCallback(async (type: string, data: object = {}, messageId?: number): Promise<ChatAck> => {
        if (!transport.current) throw new Error("Нет соединения с чатом.");
        const result = await transport.current.command(type, data, messageId);
        if (result.message) client.setQueriesData<ChatHistory>({ queryKey: chatKeys.messages(projectId, userId) }, (old) => applyChatMessage(old, result.message!));
        if (result.last_read_seq !== undefined) client.setQueryData<ProjectChat>(chatKeys.info(projectId, userId), (old) => old && ({ ...old, last_read_seq: Math.max(old.last_read_seq, result.last_read_seq!), unread_count: result.unread_count ?? old.unread_count }));
        return result;
    }, [client, projectId, userId]);

    const send = useCallback<ProjectChatContextValue["send"]>(async (body, presentation) => {
        const current = client.getQueryData<ProjectChat>(chatKeys.info(projectId, userId));
        const member = current?.members.find((item) => item.id === userId) ?? { id: userId, username: "", display_name: "Вы" };
        const optimistic: ChatMessage = {
            id: -Date.now(), seq: Number.MAX_SAFE_INTEGER, chat_id: chatId ?? 0, project_id: projectId, author_type: "USER", author: member,
            client_message_id: body.client_message_id, content: body.content, revision: 1, created_at: new Date().toISOString(), edited_at: null, deleted_at: null,
            reply: presentation?.reply ?? null, mentions: current?.members.filter((item) => body.mentions.includes(item.id)) ?? [], entities: presentation?.entities ?? [], attachments: presentation?.attachments ?? [], reactions: [], delivery: "sending", pendingBody: body,
        };
        client.setQueryData<ChatHistory>(chatKeys.messages(projectId, userId), (old) => applyChatMessage(old, optimistic));
        try {
            const result = await command("message.send", body);
            if (!result.message) throw new Error("Сервер не вернул сообщение.");
            return result.message;
        } catch (error) {
            client.setQueryData<ChatHistory>(chatKeys.messages(projectId, userId), (old) => applyChatMessage(old, { ...optimistic, delivery: "failed", error: error instanceof Error ? error.message : "Не удалось отправить сообщение." }));
            throw error;
        }
    }, [chatId, client, command, projectId, userId]);

    const markRead = useCallback(async (message: ChatMessage) => {
        const boundary = client.getQueryData<ProjectChat>(chatKeys.info(projectId, userId))?.last_read_seq ?? 0;
        if (!canWrite || message.delivery || message.seq <= boundary || state !== "connected") return;
        await command("read.set", { message_id: message.id });
    }, [client, projectId, userId, canWrite, state, command]);
    const signalTyping = useCallback((active: boolean) => transport.current?.ephemeral(active ? "typing.started" : "typing.stopped"), []);
    const dismissTaskMove = useCallback((seq: number) => setMoveNotices((old) => ({ ...old, items: old.items.filter((row) => row.seq !== seq) })), []);
    const value = useMemo<ProjectChatContextValue>(() => ({ projectId, projectKey, userId, info: info.data, loading: info.isPending, error: info.error, state, online, typing: typing.filter((id) => id !== userId), canWrite, command, send, markRead, signalTyping, taskMoves, dismissTaskMove }), [projectId, projectKey, userId, info.data, info.isPending, info.error, state, online, typing, canWrite, command, send, markRead, signalTyping, taskMoves, dismissTaskMove]);
    const inaccessible = info.error instanceof ApiError && [403, 404].includes(info.error.status);

    return <ProjectChatContext.Provider value={value}>
        {children}
        {notification && notification.project_id === projectId && !inaccessible && !chatVisible && <aside aria-label="Новое сообщение проекта" aria-live="polite" className="glass fixed top-5 right-5 z-50 flex w-80 max-w-[90vw] gap-3 rounded-lg border border-line-subtle p-3 shadow-panel">
            <MessageSquare size={17} className="mt-1 shrink-0 text-accent" aria-hidden="true" />
            <button className="min-w-0 flex-1 text-left" onClick={() => { navigate(`/projects/${projectKey}/chat?message=${notification.id}`); setNotification(null); }}>
                <span className="block text-xs font-medium text-primary">{notification.mentions.some((item) => item.id === userId) ? "Вас упомянули" : notification.reply?.author?.id === userId ? "Ответ на ваше сообщение" : "Новое сообщение"} · {projectKey}</span>
                <span className="mt-1 block truncate text-xs text-secondary">{chatAuthorName(notification)}: {notification.content || "Вложение"}</span>
            </button>
            <IconButton label="Скрыть уведомление чата" size="sm" onClick={() => setNotification(null)}><X size={13} /></IconButton>
        </aside>}
    </ProjectChatContext.Provider>;
}
