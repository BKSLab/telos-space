import { UserAvatar } from "@/components/users/UserAvatar";
import { useCurrentUser } from "@/lib/useAuth";
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { useLocation, useSearchParams } from "react-router-dom";
import { ArrowDown, Eye, Paperclip, Plus, Send, X } from "lucide-react";
import { AgentFileList } from "@/components/files/AgentFileList";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { AgentFile, AgentMessage, Project, Task } from "@/lib/types";
import { draftUploadProblem, useDraftUploads } from "@/lib/useDraftUploads";
import { DraftUploadList } from "@/components/files/DraftUploadList";
import { useArisConversations } from "@/lib/useArisConversations";
import { describePageContext, ARIS_NAME, ARIS_PERSONA } from "@/lib/aris";
import { useArisPageContext } from "@/lib/useArisPageContext";
import { actionSources } from "@/lib/agentCards";
import { cn } from "@/lib/cn";
import { useArisWidgetStore } from "@/stores/arisWidget";
import { Button, IconButton } from "@/components/ui/Button";
import { ErrorMessage } from "@/components/ui/States";
import { AgentActionCard } from "@/components/projects/AgentActionCard";
import { AgentEntityCard } from "@/components/agent/AgentEntityCard";
import { ArisAvatar } from "@/components/brand/ArisAvatar";
import { ArisMarkdown, ArisThinking } from "@/components/chat/ArisChatReply";
import { newUuid } from "@/lib/uuid";
import { isTouchChatInput, shouldSubmitChat, useChatInput } from "@/components/chat/useChatInput";
import { useChatHistory } from "@/components/chat/useChatHistory";
import { ChatMessageReferences } from "@/components/chat/ChatMessageReferences";
import { chatMessageId } from "@/lib/chatMessageReferences";
import { browserTimezone } from "@/lib/timezone";
import "@/components/chat/chat.css";

/** Панель личного диалога с Арисом (docs/agent.md, 3). */
export function ArisPanel({ userId, onClose, embedded = false }: { userId: number; onClose?: () => void; embedded?: boolean }) {
    const currentUser = useCurrentUser().data;
    const location = useLocation();
    const [params, setParams] = useSearchParams();
    const personalRoute = embedded && location.pathname.replace(/\/$/, "") === "/chat";
    const requestedId = personalRoute ? chatMessageId(params.get("conversation")) : null;
    const around = requestedId != null ? chatMessageId(params.get("message")) : null;
    const mounted = useRef(true);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
    const context = useArisPageContext();
    const projects = useQuery({ queryKey: queryKeys.projects, queryFn: () => api.get<Project[]>(endpoints.projects()) }).data ?? [];
    const task = useQuery({ queryKey: queryKeys.task(context.task_id ?? 0), queryFn: () => api.get<Task>(endpoints.task(context.task_id!)), enabled: context.task_id != null }).data;
    const pageProject = context.project_key ? projects.find((item) => item.key.toUpperCase() === context.project_key!.toUpperCase()) : undefined;
    const seen = describePageContext(context, { project: pageProject?.key, taskKey: task?.id === context.task_id ? task?.key : null });

    const remembered = useArisWidgetStore((state) => state.conversations[userId]);
    const select = useArisWidgetStore((state) => state.select);
    const updateDraft = useArisWidgetStore((state) => state.updateDraft);
    const clearDraft = useArisWidgetStore((state) => state.clearDraft);
    const chat = useArisConversations(requestedId ?? remembered ?? null, true, around);
    const conversations = chat.conversations.data?.pages.flatMap((page) => page.items) ?? [];
    const firstId = conversations[0]?.id;
    // Текущий диалог — первый в списке сервера, пока пользователь не выбрал другой.
    const conversationId = requestedId ?? remembered ?? firstId ?? null;
    useEffect(() => { if (requestedId != null && requestedId !== remembered) select(userId, requestedId); }, [requestedId, remembered, select, userId]);
    const messages = chat.messages.data?.pages.slice().reverse().flatMap((page) => page.items) ?? [];
    const draftKey = `${userId}:${conversationId ?? "new"}`;
    const draft = useArisWidgetStore((state) => state.drafts[draftKey]);
    const question = draft?.content ?? "";
    const selectedFiles = draft?.files ?? [];
    const uploads = useDraftUploads<{ file: AgentFile; conversationId: number; key: string }>(draftKey);
    const [uploadError, setUploadError] = useState<string | null>(null);
    useEffect(() => {
        if (requestedId != null || remembered != null || firstId == null || uploads.blocked) return;
        const key = `${userId}:new`;
        const current = useArisWidgetStore.getState().drafts[key];
        if (current) { updateDraft(`${userId}:${firstId}`, current); clearDraft(key, current.requestId); }
        select(userId, firstId);
    }, [requestedId, remembered, firstId, uploads.blocked, select, userId, updateDraft, clearDraft]);
    const busy = messages.some((message) => message.status === "queued" || message.status === "processing");
    const sending = chat.mutating;
    const sendingRef = useRef(false);
    const loading = chat.conversations.isPending || (conversationId != null && chat.messages.isPending);
    const chatError = chat.conversations.error || chat.messages.error || chat.create.error || chat.send.error || chat.retry.error || chat.decide.error;
    const lastMessage = messages.at(-1);
    const panel = useRef<HTMLElement>(null);
    const input = useChatInput(question);
    const { viewportRef, contentRef, atBottom, onScroll, scrollToLatest } = useChatHistory(`${draftKey}:${around ?? "latest"}`, messages.map(message => `${message.id}:${message.status}:${message.content.length}`).join("|"), around);
    const fileInput = useRef<HTMLInputElement>(null);
    useEffect(() => { if (around == null && !isTouchChatInput()) input.current?.focus(); }, [input, around]);

    function resetErrors() {
        setUploadError(null);
        chat.send.reset(); chat.retry.reset(); chat.decide.reset(); chat.upload.reset(); chat.removeUpload.reset(); chat.create.reset();
    }
    function showLatest(id = conversationId) {
        if (personalRoute && mounted.current) setParams(previous => { const next = new URLSearchParams(previous); next.delete("message"); if (id != null) next.set("conversation", String(id)); return next; });
        scrollToLatest();
    }
    function choose(id: number) { select(userId, id); showLatest(id); resetErrors(); }

    async function ensureConversation(): Promise<number> {
        if (conversationId != null) return conversationId;
        const id = (await chat.create.mutateAsync()).id;
        select(userId, id);
        return id;
    }

    async function submit(value = question) {
        const normalized = value.trim();
        if (normalized.length < 2 || sending || sendingRef.current || uploads.isBlocked() || busy || loading || chat.messages.isError) return;
        sendingRef.current = true;
        scrollToLatest();
        const requestId = draft?.content.trim() === normalized ? draft.requestId : newUuid();
        const pageContext = draft?.submission?.requestId === requestId ? draft.submission.pageContext : { ...context, timezone: browserTimezone() };
        const submitted = { content: normalized, files: selectedFiles, requestId, submission: { requestId, pageContext } };
        updateDraft(draftKey, submitted);
        try {
            const id = await ensureConversation();
            const key = `${userId}:${id}`;
            if (key !== draftKey) { updateDraft(key, submitted); clearDraft(draftKey, requestId); }
            await chat.send.mutateAsync({ id, content: normalized, requestId, fileIds: selectedFiles.map((file) => file.id), pageContext });
            clearDraft(key, requestId);
            showLatest(id);
        } catch {
            // Черновик и UUID остаются: повтор не создаст второй вопрос.
        } finally {
            sendingRef.current = false;
        }
    }

    async function newConversation() {
        if (sending || sendingRef.current) return;
        sendingRef.current = true;
        try { choose((await chat.create.mutateAsync()).id); } catch { /* Ошибка показывается над полем ввода. */ } finally { sendingRef.current = false; }
    }

    function uploadFiles(files: File[]) {
        if (sending || sendingRef.current || busy || loading) return;
        const problem = draftUploadProblem(files, selectedFiles.length + uploads.count);
        setUploadError(problem);
        if (problem) return;
        let conversation: Promise<number> | undefined;
        let moved = false;
        uploads.add(files, {
            upload: async (file, operation) => {
                const id = await (conversation ??= (conversationId == null ? chat.create.mutateAsync().then(value => value.id) : Promise.resolve(conversationId))
                    .catch(cause => { conversation = undefined; throw cause; }));
                if (operation.cancelled()) throw new Error("Загрузка отменена");
                const key = `${userId}:${id}`;
                if (key !== draftKey && !moved) {
                    moved = true; operation.adoptScope(key);
                    const current = useArisWidgetStore.getState().drafts[draftKey];
                    if (current) { updateDraft(key, current); clearDraft(draftKey, current.requestId); }
                    select(userId, id);
                }
                return { file: await chat.upload.mutateAsync({ id, file }), conversationId: id, key };
            },
            attach: item => updateDraft(item.key, { files: [...(useArisWidgetStore.getState().drafts[item.key]?.files ?? []), item.file] }),
            discard: item => api.delete(`${endpoints.arisFiles(item.conversationId)}/${item.file.id}`),
        });
    }

    async function removeFile(fileId: string) {
        if (conversationId == null || sending || busy) return;
        try {
            await uploads.remove(fileId, async () => {
                await chat.removeUpload.mutateAsync({ id: conversationId, fileId });
                const current = useArisWidgetStore.getState().drafts[draftKey];
                if (current) updateDraft(draftKey, { files: current.files.filter(file => file.id !== fileId) });
            });
        } catch (cause) { setUploadError((cause as Error).message); }
    }

    function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
        if (shouldSubmitChat(event)) { event.preventDefault(); void submit(); }
    }

    /** Проект карточек ответа — со страницы, откуда задан вопрос (у ответа контекста нет). */
    function answerProject(message: AgentMessage) {
        const asked = messages.find((item) => item.role === "user" && item.request_id === message.request_id);
        const key = asked?.page_context?.project_key;
        const project = key ? projects.find((item) => item.key.toUpperCase() === key.toUpperCase()) : undefined;
        return project ? { id: project.id, key: project.key } : null;
    }

    const boardStarters = context.board_selection?.length
        ? ["Выровняй выделенные объекты по верхнему краю", "Перестрой выделенную схему слева направо"]
        : ["Кратко опиши схему на этой доске", "Что можно улучшить в этой схеме?"];
    const starters = context.board_id ? boardStarters : [
        ...(context.task_id ? ["Что с этой задачей и что мешает её закрыть?"] : []),
        ...(context.project_key && !context.task_id ? ["Дай сводку по этому проекту: сроки и риски"] : []),
        "Какие мои задачи просрочены или горят на этой неделе?",
        "Что у меня в работе и что стоит сделать первым?",
    ];

    return <section ref={panel} role={embedded ? undefined : "dialog"} aria-modal={embedded ? undefined : "false"} aria-label={ARIS_PERSONA} tabIndex={-1} className={embedded ? "flex h-full min-h-0 flex-col" : "aris-panel"}
        onKeyDown={(event) => { if (!embedded && event.key === "Escape" && !event.defaultPrevented) { event.preventDefault(); event.stopPropagation(); onClose?.(); } }}>
        <header className="chat-personal-header brand-ai-header flex shrink-0 items-center gap-2 border-b border-line-subtle px-3 py-2.5">
            <ArisAvatar />
            <div className="min-w-0 flex-1">
                <h2 className="text-[13px] font-semibold text-primary">{ARIS_NAME}<span className="font-normal text-muted"> — проектный менеджер</span></h2>
                <p className="flex min-w-0 items-center gap-1 text-[11px] text-muted" data-testid="aris-context"
                    title={seen ? "Арис учитывает эту страницу: можно спросить «что с этой задачей?»" : "Арис видит ваши задачи и проекты, в которых вы участвуете"}>
                    <Eye size={11} className="shrink-0" aria-hidden="true" />
                    <span className="truncate">{seen || "Личный помощник: ваши задачи и проекты"}</span>
                </p>
            </div>
            <IconButton label="Новый диалог с Арисом — прежние останутся в списке" disabled={sending || loading} onClick={() => void newConversation()}><Plus size={16} /></IconButton>
            {!embedded && <IconButton label="Закрыть окно Ариса" onClick={onClose}><X size={16} /></IconButton>}
        </header>

        {around != null && <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-1 text-xs text-secondary"><span>Контекст сообщения</span><Button size="sm" variant="ghost" onClick={() => showLatest()}>К последним сообщениям</Button></div>}
        {conversations.length > 0 && <div className="flex shrink-0 items-center gap-2 border-b border-line-subtle px-3 py-1.5">
            <select aria-label="Диалог с Арисом" title="Прежние диалоги с Арисом — от недавних к старым" value={conversationId ?? ""} disabled={sending} onChange={(event) => choose(Number(event.target.value))}
                className="min-h-9 min-w-0 flex-1 rounded-[var(--radius-control)] border border-line bg-surface px-2 py-1 text-[12px] text-primary">
                {conversationId != null && !conversations.some((item) => item.id === conversationId) && <option value={conversationId}>Текущий диалог</option>}
                {conversations.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
            </select>
            {chat.conversations.hasNextPage && <Button size="sm" variant="ghost" disabled={chat.conversations.isFetchingNextPage} onClick={() => void chat.conversations.fetchNextPage()}>Ещё</Button>}
        </div>}

        <div className="relative min-h-0 flex-1">
        <div ref={viewportRef} onScroll={onScroll} aria-label="История личного диалога" className="chat-history scrollbar-thin h-full overflow-y-auto px-3 py-4" aria-live="polite">
            <div ref={contentRef}>
            {loading ? <p className="text-[13px] text-muted">Загружаю переписку…</p> : messages.length === 0 ? (
                <div className="flex flex-col gap-3 py-4 text-center">
                    <ArisAvatar size={48} className="self-center" />
                    <p className="text-[13px] text-secondary">Спросите Ариса о своих задачах, сроках и проектах. Обращаться «Арис, …» здесь не нужно — этот диалог видите только вы.</p>
                    <div className="grid border-y border-line-subtle">
                        {starters.map((starter) => <button key={starter} type="button" disabled={sending || Boolean(chatError)} onClick={() => void submit(starter)}
                            className="chat-starter px-3 py-2 text-left text-[12px] text-secondary transition-colors hover:bg-ai-soft hover:text-primary">{starter}</button>)}
                    </div>
                </div>
            ) : <div className="flex flex-col gap-3">
                {chat.messages.hasNextPage && <Button size="sm" variant="ghost" disabled={chat.messages.isFetchingNextPage} onClick={() => void chat.messages.fetchNextPage()}>Предыдущие сообщения</Button>}
                {messages.map((message) => {
                    const project = message.role === "assistant" ? answerProject(message) : null;
                    const actions = message.actions ?? [];
                    const asked = message.role === "user" && message.page_context ? describePageContext(message.page_context) : "";
                    return <div key={message.id} data-chat-message={message.id} data-highlighted={message.id === around || undefined} tabIndex={message.id === around ? -1 : undefined} aria-label={message.id === around ? "Выбранное сообщение" : undefined} className={cn("flex gap-2 rounded-lg", message.id === around && "ring-2 ring-accent/60", message.role === "user" ? "justify-end" : "justify-start")}>
                        {message.role === "assistant" && <ArisAvatar className="mt-1" />}
                        {message.role === "user" && <UserAvatar userId={userId} hasAvatar={currentUser?.id === userId && currentUser.has_avatar} size="sm" className="mt-1" />}
                        <div data-role={message.role} className={cn("chat-content chat-personal-message min-w-0 max-w-[92%] rounded-lg px-3 py-2.5", message.role === "user" ? "bg-accent/85 text-on-accent" : "aris-message text-secondary")}>
                            {message.status === "queued" || message.status === "processing" ? <ArisThinking progress={message.progress} />
                                : message.status === "failed" ? <div className="space-y-2 text-[12px]">
                                    <p className="text-danger">{message.error || "Не получилось ответить."}</p>
                                    {message.id === lastMessage?.id && <Button size="sm" variant="ai" disabled={sending} title="Арис попробует ответить на тот же вопрос ещё раз" onClick={() => chat.retry.mutate({ id: conversationId!, messageId: message.id })}>Повторить</Button>}
                                </div>
                                : message.role === "assistant" ? (message.content.trim() ? <ArisMarkdown content={message.content} /> : null)
                                : <p className="whitespace-pre-wrap text-[14px] leading-relaxed">{message.content}</p>}
                            {asked && <p className="mt-1 text-[10px] opacity-75" title="Страница, с которой задан вопрос — её видел Арис">{asked}</p>}
                            {message.role === "assistant" && Boolean(message.message_references?.length) && !chat.messages.isError && chat.messages.isFetchedAfterMount !== false && <div className="mt-2"><ChatMessageReferences references={message.message_references} /></div>}
                            {(message.files?.length ?? 0) > 0 && <div className="mt-2"><AgentFileList files={message.files!} conversationId={message.conversation_id} /></div>}
                            {actions.map((action) => <AgentActionCard key={action.id} action={action} project={project} sources={message.sources}
                                pending={chat.decide.isPending && chat.decide.variables?.actionId === action.id}
                                onDecision={(decision) => chat.decide.mutate({ id: conversationId!, actionId: action.id, decision })}
                                rules={{ enabled: message.id === lastMessage?.id && !busy && !sending, approveHint: "Выполнить от вашего имени. Отменить можно только вручную" }}
                            />)}
                            {message.sources?.length > 0 && <div className="mt-2 grid gap-2">
                                {message.sources.filter((source) => !actions.some((action) => actionSources(action).some((item) => item.source_id === source.source_id)))
                                    .map((source) => <AgentEntityCard key={source.source_id} source={source} project={project} />)}
                            </div>}
                        </div>
                    </div>;
                })}
            </div>}
            </div>
        </div>
        {!atBottom && around == null && <Button size="sm" className="material-glass absolute right-3 bottom-3 shadow-panel" icon={<ArrowDown size={14} />} onClick={scrollToLatest}>К последним сообщениям</Button>}
        </div>

        <form onSubmit={(event: FormEvent) => { event.preventDefault(); void submit(); }} className="chat-personal-composer shrink-0 border-t border-line-subtle px-3 py-2.5">
            {chatError && <ErrorMessage title="Арис сейчас недоступен" message={(chatError as Error).message} />}
            {uploadError && <p role="alert" className="mb-2 text-xs text-danger">{uploadError}</p>}
            <DraftUploadList entries={uploads.entries} onCancel={uploads.cancel} onRetry={uploads.retry} />
            {selectedFiles.length > 0 && conversationId !== null && <div aria-label="Файлы для отправки" className="chat-draft-items mb-2 flex"><AgentFileList files={selectedFiles} conversationId={conversationId} onRemove={id => void removeFile(id)} removeDisabled={sending || busy || uploads.removing} /></div>}
            <div className="flex items-end gap-1.5 rounded-[var(--radius-card)] border border-ai-border bg-surface p-1.5 focus-within:border-ai-blue/55">
                <input type="file" multiple ref={fileInput} aria-label="Файлы для Ариса" className="hidden" onChange={(event) => { void uploadFiles(Array.from(event.target.files ?? [])); event.target.value = ""; }} />
                <IconButton label="Приложить файл для Ариса: до 5 штук, до 10 МБ каждый" disabled={sending || busy || loading || selectedFiles.length + uploads.count >= 5} onClick={() => fileInput.current?.click()}><Paperclip size={15} /></IconButton>
                <textarea ref={input} value={question} rows={1} maxLength={2000}
                    onChange={(event) => updateDraft(draftKey, { content: event.target.value })} onKeyDown={onKeyDown}
                    placeholder={busy ? "Арис отвечает — можно подготовить следующий вопрос…" : "Спросите Ариса о задачах, сроках, проектах…"} aria-label="Вопрос Арису"
                    disabled={(sending && !chat.create.isPending) || (loading && !uploads.blocked)}
                    className="chat-input scrollbar-thin min-h-9 min-w-0 flex-1 resize-none bg-transparent px-1 py-1 text-[14px] leading-relaxed text-primary outline-none placeholder:text-faint" />
                <IconButton label="Отправить Арису (Enter)" title="Отправить Арису" variant="ai" type="submit" disabled={question.trim().length < 2 || sending || uploads.blocked || busy || loading || chat.messages.isError}><Send size={14} /></IconButton>
            </div>
            <p className="chat-personal-help mt-1 text-[10px] text-muted">Видите только вы.<span className="chat-keyboard-hint hidden sm:inline"> Enter — отправить, Shift+Enter — новая строка.</span><span className="chat-personal-help-detail"> Изменения Арис выполняет по вашему поручению; опасные действия требуют подтверждения.</span></p>
        </form>
    </section>;
}
