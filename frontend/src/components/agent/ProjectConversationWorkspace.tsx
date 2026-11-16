import { UserAvatar } from "@/components/users/UserAvatar";
import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
    ArrowDown,
    Paperclip,
    Plus,
    Send,
} from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import type {
    AgentFile,
    Project,
} from "@/lib/types";
import { useCurrentUser } from "@/lib/useAuth";
import { useProjectConversationStore } from "@/stores/projectConversations";
import { useProjectConversations } from "@/lib/useProjectConversations";
import { useRenderedMarkdown } from "@/lib/useRenderedMarkdown";
import { ArisThinking } from "@/components/chat/ArisChatReply";
import { AgentEntityCard } from "@/components/agent/AgentEntityCard";
import { AgentContextReferences } from "@/components/agent/AgentContextReferences";
import { AgentFileList } from "@/components/files/AgentFileList";
import { actionSources } from "@/lib/agentCards";
import { Button, IconButton } from "@/components/ui/Button";
import { AgentActionCard } from "@/components/projects/AgentActionCard";
import { ErrorMessage } from "@/components/ui/States";
import { cn } from "@/lib/cn";
import { ARIS_PERSONA } from "@/lib/aris";
import { ProjectKnowledgePanel } from "@/components/knowledge/ProjectKnowledgePanel";
import { useProjectKnowledgeStatus } from "@/lib/useProjectKnowledgeStatus";
import { newUuid } from "@/lib/uuid";
import { draftUploadProblem, useDraftUploads } from "@/lib/useDraftUploads";
import { DraftUploadList } from "@/components/files/DraftUploadList";
import { ArisAvatar } from "@/components/brand/ArisAvatar";
import { shouldSubmitChat, useChatInput } from "@/components/chat/useChatInput";
import { useChatHistory } from "@/components/chat/useChatHistory";
import { ChatMessageReferences } from "@/components/chat/ChatMessageReferences";
import { chatMessageId } from "@/lib/chatMessageReferences";
import { useArisPageContext } from "@/lib/useArisPageContext";
import { browserTimezone } from "@/lib/timezone";
import "@/components/chat/chat.css";

const STARTERS = [
    "Дай краткую сводку проекта и текущие риски",
    "Какие задачи просрочены и кто за них отвечает?",
    "Что уже сделано, а что сейчас в работе?",
    "Какие решения и требования зафиксированы в документах?",
];

function MarkdownAnswer({ content }: { content: string }) {
    const html = useRenderedMarkdown(content);
    return (
        <div
            className="markdown-body text-[13px]"
            // HTML очищается DOMPurify внутри общего Markdown-renderer.
            dangerouslySetInnerHTML={{ __html: html }}
        />
    );
}

export function ProjectConversationWorkspace({ project }: { project: Project }) {
    const navigate = useNavigate();
    const location = useLocation();
    const queryClient = useQueryClient();
    const [searchParams] = useSearchParams();
    const updateSearchParams = useCallback((update: (previous: URLSearchParams) => URLSearchParams, options?: { replace?: boolean }) => {
        const next = update(new URLSearchParams(location.search));
        navigate({ pathname: location.pathname, search: next.toString(), hash: location.hash }, options);
    }, [location, navigate]);
    const currentUser = useCurrentUser().data;
    const userId = currentUser?.id ?? 0;
    const sessionKey = `${userId}:${project.id}`;
    const rememberedId = useProjectConversationStore((state) => state.conversations[sessionKey]);
    const select = useProjectConversationStore((state) => state.select);
    const updateDraft = useProjectConversationStore((state) => state.updateDraft);
    const clearDraft = useProjectConversationStore((state) => state.clearDraft);
    const context = useArisPageContext();
    const requestedId = chatMessageId(searchParams.get("conversation"));
    const conversationId = requestedId ?? rememberedId ?? null;
    const around = requestedId != null ? chatMessageId(searchParams.get("message")) : null;
    const mounted = useRef(true);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
    useEffect(() => {
        if (conversationId != null && conversationId !== rememberedId) select(sessionKey, conversationId);
    }, [conversationId, rememberedId, select, sessionKey]);
    const chat = useProjectConversations(project.id, conversationId, around);
    const conversations = chat.conversations.data?.pages.flatMap((page) => page.items) ?? [];
    const messages = chat.messages.data?.pages.slice().reverse().flatMap((page) => page.items) ?? [];
    const fileInputRef = useRef<HTMLInputElement>(null);
    const draftKey = `${sessionKey}:${conversationId ?? "new"}`;
    const draft = useProjectConversationStore((state) => state.drafts[draftKey]);
    const question = draft?.content ?? "";
    const input = useChatInput(question);
    const { viewportRef, contentRef, atBottom, onScroll, scrollToLatest } = useChatHistory(`${draftKey}:${around ?? "latest"}`, messages.map(message => `${message.id}:${message.status}:${message.content.length}`).join("|"), around);
    const selectedFiles = draft?.files ?? [];
    const uploads = useDraftUploads<{ file: AgentFile; conversationId: number; key: string }>(draftKey);
    const [uploadError, setUploadError] = useState<string | null>(null);
    const sendingRef = useRef(false);
    const busy = messages.some((message) => message.status === "queued" || message.status === "processing");
    const sending = chat.mutating;
    const loading = chat.conversations.isPending || (conversationId != null && chat.messages.isPending);
    const chatError = chat.conversations.error || chat.messages.error || chat.create.error || chat.send.error || chat.retry.error || chat.decide.error;
    const lastMessage = messages.at(-1);

    function selectConversation(id: number) {
        setUploadError(null);
        select(sessionKey, id);
        if (mounted.current) updateSearchParams((previous) => { const next = new URLSearchParams(previous); next.set("conversation", String(id)); next.delete("message"); return next; });
        chat.send.reset();
        chat.retry.reset();
        chat.decide.reset();
        chat.upload.reset();
        chat.removeUpload.reset();
    }

    function showLatest(id = conversationId) {
        if (mounted.current) updateSearchParams(previous => { const next = new URLSearchParams(previous); next.delete("message"); if (id != null) next.set("conversation", String(id)); return next; });
        scrollToLatest();
    }

    const firstConversationId = conversations[0]?.id;
    const completedActions = messages.flatMap((message) => message.actions ?? []).filter((action) => action.status === "completed").map((action) => action.id).join(",");
    useEffect(() => {
        if (!completedActions) return;
        let active = true;
        async function refreshProject() {
            try {
                const current = await api.get<Project>(endpoints.project(project.id));
                if (!active) return;
                if (current.key !== project.key) {
                    queryClient.setQueryData<Project[]>(queryKeys.projects, (previous) => previous?.map((item) => item.id === current.id ? current : item));
                    const prefix = `/projects/${project.key}`;
                    if (window.location.pathname === prefix || window.location.pathname.startsWith(`${prefix}/`)) {
                        navigate(`${window.location.pathname.replace(prefix, `/projects/${current.key}`)}${window.location.search}`, { replace: true });
                    }
                }
            } finally {
                if (active) void queryClient.invalidateQueries({ predicate: (query) => {
                    const [kind, id, section] = query.queryKey;
                    return (kind === "projects" && (id == null || id === project.id) && section !== "aris")
                        || kind === "tasks" || kind === "documents" || kind === "dashboard";
                } });
            }
        }
        void refreshProject().catch(() => { /* Общие запросы покажут актуальное состояние доступа. */ });
        return () => { active = false; };
    }, [completedActions, project.id, project.key, queryClient, navigate]);
    useEffect(() => {
        if (conversationId == null && firstConversationId != null && !uploads.blocked) {
            const key = `${sessionKey}:new`;
            const current = useProjectConversationStore.getState().drafts[key];
            if (current) { updateDraft(`${sessionKey}:${firstConversationId}`, current); clearDraft(key, current.requestId); }
            select(sessionKey, firstConversationId);
            updateSearchParams((previous) => { const next = new URLSearchParams(previous); next.set("conversation", String(firstConversationId)); return next; }, { replace: true });
        }
    }, [conversationId, firstConversationId, uploads.blocked, select, sessionKey, updateSearchParams, updateDraft, clearDraft]);

    const statusQuery = useProjectKnowledgeStatus(project.id);

    async function submit(value = question) {
        const normalized = value.trim();
        if (normalized.length < 2 || sending || sendingRef.current || uploads.isBlocked() || busy || loading || chat.messages.isError) return;
        sendingRef.current = true;
        scrollToLatest();
        const requestId = draft?.content.trim() === normalized ? draft.requestId : newUuid();
        const pageContext = draft?.submission?.requestId === requestId ? draft.submission.pageContext : { ...context, timezone: browserTimezone() };
        const submitted = { content: normalized, files: selectedFiles, requestId, submission: { requestId, pageContext } };
        updateDraft(draftKey, submitted);
        let id = conversationId;
        try {
            if (id == null) {
                id = (await chat.create.mutateAsync()).id;
                updateDraft(`${sessionKey}:${id}`, submitted);
                clearDraft(draftKey, requestId);
                selectConversation(id);
            }
            await chat.send.mutateAsync({ id, content: normalized, requestId, fileIds: selectedFiles.map((file) => file.id), pageContext });
            clearDraft(draftKey, requestId);
            clearDraft(`${sessionKey}:${id}`, requestId);
            showLatest(id);
        } catch {
            // Черновик и UUID сохраняются для повтора в любом представлении чата.
        } finally {
            sendingRef.current = false;
        }
    }

    async function newConversation() {
        if (sending || sendingRef.current) return;
        sendingRef.current = true;
        try {
            selectConversation((await chat.create.mutateAsync()).id);
        } catch {
            // Состояние ошибки отображается рядом с редактором сообщения.
        } finally {
            sendingRef.current = false;
        }
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
                const key = `${sessionKey}:${id}`;
                if (key !== draftKey && !moved) {
                    moved = true; operation.adoptScope(key);
                    const current = useProjectConversationStore.getState().drafts[draftKey];
                    if (current) { updateDraft(key, current); clearDraft(draftKey, current.requestId); }
                    selectConversation(id);
                }
                return { file: await chat.upload.mutateAsync({ id, file }), conversationId: id, key };
            },
            attach: item => updateDraft(item.key, { files: [...(useProjectConversationStore.getState().drafts[item.key]?.files ?? []), item.file] }),
            discard: item => api.delete(`${endpoints.projectArisFiles(project.id, item.conversationId)}/${item.file.id}`),
        });
    }

    async function removeFile(fileId: string) {
        if (conversationId == null || sending || busy) return;
        try {
            await uploads.remove(fileId, async () => {
                await chat.removeUpload.mutateAsync({ id: conversationId, fileId });
                const current = useProjectConversationStore.getState().drafts[draftKey];
                if (current) updateDraft(draftKey, { files: current.files.filter(file => file.id !== fileId) });
            });
        } catch (cause) { setUploadError((cause as Error).message); }
    }

    function onSubmit(event: FormEvent) {
        event.preventDefault();
        void submit();
    }

    function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
        if (shouldSubmitChat(event)) {
            event.preventDefault();
            void submit();
        }
    }

    const status = statusQuery.data;
    const indexing = Boolean(status && (status.pending_jobs > 0 || status.processing_jobs > 0));

    return (
        <div className="h-full min-h-0">
            <div className="chat-project-conversations mx-auto grid h-full w-full max-w-6xl gap-4 p-5 lg:grid-cols-[minmax(0,1fr)_280px]">
                <section aria-label="Личный диалог с Арисом" className="brand-ai-panel flex min-h-0 min-w-0 flex-col overflow-hidden rounded-[var(--radius-panel)] border border-transparent shadow-card">
                    <div className="chat-agent-header brand-ai-header flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-line-subtle px-3 py-2 sm:gap-3 sm:px-4 sm:py-3">
                        <div className="flex min-w-0 items-center gap-2.5">
                            <ArisAvatar />
                            <div className="min-w-0">
                                <h2 className="text-[13px] font-semibold text-primary">{ARIS_PERSONA}</h2>
                                <p className="truncate text-[11px] text-muted">
                                    {project.name} · видите только вы
                                </p>
                            </div>
                        </div>
                        <span
                            className={cn(
                                "rounded-[5px] border px-2 py-0.5 text-[10px] font-medium",
                                status?.ready
                                    ? "border-success/30 bg-success/10 text-success"
                                    : "border-warning/30 bg-warning/10 text-warning",
                            )}
                        >
                            {status?.ready ? "Вики готова" : indexing ? "Обновление знаний" : status?.enabled ? "Контекст неполный" : "Поиск по данным проекта"}
                        </span>
                    </div>

                    <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-line-subtle px-3 py-2 sm:px-4">
                        <select aria-label="Диалог" value={conversationId ?? ""}
                            disabled={sending || chat.conversations.isPending}
                            onChange={(event) => selectConversation(Number(event.target.value))}
                            className="min-h-9 min-w-0 flex-1 rounded-[var(--radius-control)] border border-line bg-surface px-2 py-1.5 text-[12px] text-primary">
                            {conversationId == null && <option value="">Начните первый разговор</option>}
                            {conversationId != null && !conversations.some((item) => item.id === conversationId) && <option value={conversationId}>Текущий диалог</option>}
                            {conversations.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
                        </select>
                        <Button size="sm" icon={<Plus size={13} />} disabled={sending || loading} onClick={() => void newConversation()}>Новый диалог</Button>
                        {chat.conversations.hasNextPage && <Button size="sm" disabled={chat.conversations.isFetchingNextPage} onClick={() => void chat.conversations.fetchNextPage()}>Ещё диалоги</Button>}
                    </div>

                    {around != null && <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-1 text-xs text-secondary"><span>Контекст сообщения</span><Button size="sm" variant="ghost" onClick={() => showLatest()}>К последним сообщениям</Button></div>}
                    <div className="relative min-h-0 flex-1">
                    <div ref={viewportRef} onScroll={onScroll} aria-label="История личного диалога проекта" className="chat-history scrollbar-thin h-full overflow-y-auto px-3 py-4 sm:px-4 sm:py-5" aria-live="polite">
                        <div ref={contentRef}>
                        {loading ? <p className="text-[13px] text-muted">Загружаю переписку…</p> : messages.length === 0 ? (
                            <div className="mx-auto flex h-full max-w-xl flex-col items-center justify-center gap-5 py-8 text-center">
                                <ArisAvatar size={48} />
                                <div className="flex flex-col gap-1.5">
                                    <h3 className="text-base font-semibold text-primary">
                                        Спросите что угодно о {project.name}
                                    </h3>
                                    <p className="text-[13px] text-muted">
                                        Арис учитывает знания проекта и актуальное состояние задач.
                                    </p>
                                </div>
                                <div className="grid w-full border-y border-line-subtle sm:grid-cols-2">
                                    {STARTERS.map((starter) => (
                                        <button
                                            key={starter}
                                            type="button"
                                            disabled={sending || busy || Boolean(chatError)}
                                            onClick={() => void submit(starter)}
                                            className="chat-starter px-3 py-2.5 text-left text-[12px] text-secondary transition-colors hover:bg-ai-soft hover:text-primary sm:odd:border-r sm:odd:border-line-subtle"
                                        >
                                            {starter}
                                        </button>
                                    ))}
                                </div>
                            </div>
                        ) : (
                            <div className="mx-auto flex max-w-3xl flex-col gap-4">
                                {chat.messages.hasNextPage && <Button size="sm" disabled={chat.messages.isFetchingNextPage} onClick={() => void chat.messages.fetchNextPage()}>Предыдущие сообщения</Button>}
                                {messages.map((message) => (
                                    <div
                                        key={message.id}
                                        data-chat-message={message.id}
                                        data-highlighted={message.id === around || undefined}
                                        tabIndex={message.id === around ? -1 : undefined}
                                        aria-label={message.id === around ? "Выбранное сообщение" : undefined}
                                        className={cn(
                                            "flex gap-2 rounded-lg",
                                            message.id === around && "ring-2 ring-accent/60",
                                            message.role === "user" ? "justify-end" : "justify-start",
                                        )}
                                    >
                                        {message.role === "user" && <UserAvatar user={currentUser ?? undefined} hasAvatar={currentUser?.has_avatar ?? false} size="sm" className="mt-1" />}
                                        <div
                                            className={cn(
                                                "chat-content chat-personal-message min-w-0 max-w-[88%] rounded-lg px-3.5 py-3",
                                                message.role === "user"
                                                    ? "bg-accent/85 text-on-accent"
                                                    : "bg-ai-soft text-secondary shadow-card",
                                            )}
                                        >
                                            {message.status === "queued" || message.status === "processing" ? (
                                                <ArisThinking progress={message.progress} />
                                            ) : message.status === "failed" ? (
                                                <div className="space-y-2 text-[12px]">
                                                    <p className="text-danger">{message.error}</p>
                                                    {message.id === lastMessage?.id && <Button size="sm" variant="ai" disabled={sending} onClick={() => chat.retry.mutate({ id: conversationId!, messageId: message.id })}>Повторить ответ</Button>}
                                                </div>
                                            ) : message.role === "assistant" ? (
                                                message.content.trim() ? <MarkdownAnswer content={message.content} /> : null
                                            ) : (
                                                <p className="whitespace-pre-wrap text-[14px] leading-relaxed">
                                                    {message.content}
                                                </p>
                                            )}
                                            {message.role === "assistant" && Boolean(message.message_references?.length) && !chat.messages.isError && chat.messages.isFetchedAfterMount !== false && <div className="mt-2"><ChatMessageReferences references={message.message_references} /></div>}
                                            {(message.files?.length ?? 0) > 0 && <div className="mt-2"><AgentFileList files={message.files!} conversationId={message.conversation_id} projectId={project.id} /></div>}
                                            {(message.actions ?? []).map((action) => <AgentActionCard
                                                key={action.id} action={action} project={project} sources={message.sources}
                                                rules={{ enabled: message.id === lastMessage?.id && !busy && !sending }}
                                                pending={chat.decide.isPending}
                                                onDecision={(decision) => chat.decide.mutate({ id: conversationId!, actionId: action.id, decision })}
                                            />)}
                                            <AgentContextReferences projectId={project.id} external={message.external_sources} assertions={message.assertion_references} />
                                            {message.sources?.length > 0 && <div className="mt-3 grid gap-2">
                                                {message.sources.filter((source) => !(message.actions ?? []).some((action) => actionSources(action).some((item) => item.source_id === source.source_id)))
                                                    .map((source) => <AgentEntityCard key={source.source_id} source={source} project={project} />)}
                                            </div>}

                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                        </div>
                    </div>
                    {!atBottom && around == null && <Button size="sm" className="material-glass absolute right-3 bottom-3 shadow-panel" icon={<ArrowDown size={14} />} onClick={scrollToLatest}>К последним сообщениям</Button>}
                    </div>

                    <form onSubmit={onSubmit} className="chat-personal-composer shrink-0 border-t border-line-subtle bg-surface/55 px-4 py-3">
                        {chatError && <ErrorMessage title="Не удалось обновить диалог" message={(chatError as Error).message} />}
                        {uploadError && <p role="alert" className="mb-2 text-xs text-danger">{uploadError}</p>}
                        <DraftUploadList entries={uploads.entries} onCancel={uploads.cancel} onRetry={uploads.retry} />
                        {selectedFiles.length > 0 && conversationId !== null && <div aria-label="Файлы для отправки" className="chat-draft-items mx-auto mb-2 flex max-w-3xl"><AgentFileList files={selectedFiles} conversationId={conversationId} projectId={project.id} onRemove={id => void removeFile(id)} removeDisabled={sending || busy || uploads.removing} /></div>}
                        <div className="material-metal mx-auto flex max-w-3xl items-end gap-2 rounded-[var(--radius-card)] border border-ai-border p-2 transition-[border-color,box-shadow] focus-within:border-ai-blue/55 focus-within:shadow-focus">
                            <input type="file" multiple ref={fileInputRef} aria-label="Файлы для Ариса" className="hidden" onChange={(event) => { void uploadFiles(Array.from(event.target.files ?? [])); event.target.value = ""; }} />
                            <IconButton label="Добавить файл до 10 МБ" disabled={sending || busy || loading || selectedFiles.length + uploads.count >= 5} onClick={() => fileInputRef.current?.click()}><Paperclip size={16} /></IconButton>
                            <textarea
                                ref={input}
                                value={question}
                                onChange={(event) => updateDraft(draftKey, { content: event.target.value })}
                                onKeyDown={onKeyDown}
                                rows={1}
                                maxLength={2000}
                                placeholder={busy ? "Арис отвечает — можно подготовить следующий вопрос…" : "Спросите о проекте…"}
                                aria-label="Вопрос Арису по проекту"
                                disabled={(sending && !chat.create.isPending) || (loading && !uploads.blocked)}
                                className="chat-input scrollbar-thin min-h-10 min-w-0 flex-1 resize-none bg-transparent px-1.5 py-1 text-[14px] leading-relaxed text-primary outline-none placeholder:text-disabled"
                            />
                            <Button
                                type="submit"
                                variant="ai"
                                size="md"
                                disabled={question.trim().length < 2 || sending || uploads.blocked || busy || loading || chat.messages.isError}
                                icon={<Send size={14} aria-hidden="true" />}
                            >
                                <span className="sr-only sm:not-sr-only">Спросить</span>
                            </Button>
                        </div>
                        <p className="chat-personal-help mx-auto mt-1.5 max-w-3xl text-[10px] text-disabled">
                            Переписка сохраняется.<span className="chat-keyboard-hint hidden sm:inline"> Enter — отправить, Shift+Enter — новая строка.</span>
                        </p>
                    </form>
                </section>

                <aside aria-label="Знания проекта" className="scrollbar-thin hidden min-h-0 flex-col gap-3 overflow-y-auto lg:flex"><ProjectKnowledgePanel projectId={project.id} /></aside>
            </div>
        </div>
    );
}
