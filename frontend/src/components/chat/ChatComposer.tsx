import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AtSign, CornerUpLeft, Hash, Loader2, Paperclip, Send, X } from "lucide-react";
import { api } from "@/lib/api";
import { chatAuthorName, chatKeys, chatPath, type ChatAttachment, type ChatEntity, type ChatMessage, type ChatMessageBody, type ChatUser } from "@/lib/projectChat";
import { useProjectChat } from "@/lib/useProjectChat";
import { isForTim, TIM_CHAT_HINT } from "@/lib/tim";
import { emptyChatDraft, useChatUiStore, type ChatDraft } from "@/stores/chat";
import { Button, IconButton } from "@/components/ui/Button";
import { cn } from "@/lib/cn";
import { isTouchChatInput, shouldSubmitChat, useChatInput } from "./useChatInput";
import "./chat.css";

interface Picker { kind: "@" | "#"; query: string; start: number; end: number }

export function ChatComposer({ editing, onCancelEdit }: { editing: ChatMessage | null; onCancelEdit: () => void }) {
    const chat = useProjectChat();
    const signalTyping = chat.signalTyping;
    const sessionKey = `${chat.userId}:${chat.projectId}`;
    const draftKey = editing ? `${sessionKey}:edit:${editing.id}:${editing.revision}` : sessionKey;
    const storedDraft = useChatUiStore((state) => state.drafts[draftKey]);
    const updateStored = useChatUiStore((state) => state.updateDraft);
    const clearStored = useChatUiStore((state) => state.clearDraft);
    const [initialDraft] = useState(emptyChatDraft);
    const [editDraft] = useState<ChatDraft>(() => editing ? ({ content: editing.content, mentions: editing.mentions.map((user) => user.id), entities: editing.entities, attachments: editing.attachments, reply: editing.reply, clientMessageId: editing.client_message_id }) : emptyChatDraft());
    const draft = storedDraft ?? (editing ? editDraft : initialDraft);
    const [picker, setPicker] = useState<Picker | null>(null);
    const [selection, setSelection] = useState(0);
    const [error, setError] = useState<string | null>(null);
    const sending = useChatUiStore((state) => state.busy[`${sessionKey}:send`]) ?? false;
    const uploading = useChatUiStore((state) => state.busy[`${sessionKey}:upload`]) ?? false;
    const setBusy = useChatUiStore((state) => state.setBusy);
    const setSending = (active: boolean) => setBusy(`${sessionKey}:send`, active);
    const setUploading = (active: boolean) => setBusy(`${sessionKey}:upload`, active);
    const input = useChatInput(draft.content);
    const fileInput = useRef<HTMLInputElement>(null);
    const lastTyping = useRef(0);
    const typingStop = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
    const [entityQuery, setEntityQuery] = useState("");
    useEffect(() => {
        const timer = setTimeout(() => setEntityQuery(picker?.kind === "#" ? picker.query : ""), 180);
        return () => clearTimeout(timer);
    }, [picker?.kind, picker?.query]);
    useEffect(() => () => { clearTimeout(typingStop.current); signalTyping(false); }, [signalTyping]);
    const entities = useQuery({ queryKey: [...chatKeys.entities(chat.projectId, chat.userId), entityQuery], queryFn: () => api.get<ChatEntity[]>(`${chatPath(chat.projectId)}/entities?q=${encodeURIComponent(entityQuery)}`), enabled: picker?.kind === "#", staleTime: 10000 });
    const members = (chat.info?.members ?? []).filter((user) => `${user.display_name} ${user.username}`.toLocaleLowerCase().includes((picker?.query ?? "").toLocaleLowerCase()));
    const choices: (ChatUser | ChatEntity)[] = (picker?.kind === "@" ? members : entities.data ?? []).slice(0, 30);
    const disabled = !chat.canWrite || sending;

    const update = (patch: Partial<ChatDraft>) => {
        const next = { ...patch, clientMessageId: crypto.randomUUID() };
        updateStored(draftKey, { ...draft, ...next });
    };
    const uploadMutation = useMutation({ mutationFn: (file: File) => { const data = new FormData(); data.append("file", file); return api.postForm<ChatAttachment>(`${chatPath(chat.projectId)}/attachments`, data); } });
    const upload = async (files: FileList | File[]) => {
        if (editing || !chat.canWrite || sending || useChatUiStore.getState().busy[`${sessionKey}:upload`]) return;
        setError(null);
        const selected = [...files];
        if (draft.attachments.length + selected.length > 5) { setError("В одном сообщении можно отправить до 5 файлов."); return; }
        if (selected.some((file) => file.size > 10 * 1024 * 1024 || !file.size)) { setError("Нужны непустые файлы размером до 10 МБ."); return; }
        setUploading(true);
        try {
            for (const file of selected) {
                const result = await uploadMutation.mutateAsync(file);
                const current = useChatUiStore.getState().drafts[draftKey] ?? draft;
                updateStored(draftKey, { ...current, attachments: [...current.attachments, result], clientMessageId: crypto.randomUUID() });
            }
        } catch (failure) { setError((failure as Error).message); } finally { setUploading(false); }
    };
    const removeFile = async (file: ChatAttachment) => {
        try { await api.delete(`${chatPath(chat.projectId)}/attachments/${file.id}`); update({ attachments: draft.attachments.filter((item) => item.id !== file.id) }); }
        catch (failure) { setError((failure as Error).message); }
    };
    const choose = (choice: ChatUser | ChatEntity) => {
        if (!picker) return;
        const prefix = draft.content.slice(0, picker.start);
        const suffix = draft.content.slice(picker.end);
        let insert = "";
        if ("username" in choice) {
            if (!draft.mentions.includes(choice.id) && draft.mentions.length >= 20) { setError("В сообщении можно упомянуть до 20 участников."); return; }
            insert = `@${choice.username} `;
            update({ content: `${prefix}${insert}${suffix}`, mentions: [...new Set([...draft.mentions, choice.id])] });
        } else {
            const duplicate = draft.entities.some((ref) => ref.entity_type === choice.entity_type && ref.entity_id === choice.entity_id);
            if (!duplicate && draft.entities.length >= 10) { setError("В сообщении можно добавить до 10 ссылок."); return; }
            update({ content: `${prefix}${suffix}`, entities: duplicate ? draft.entities : [...draft.entities, choice] });
        }
        setPicker(null); setSelection(0);
        requestAnimationFrame(() => { input.current?.focus(); input.current?.setSelectionRange(prefix.length + insert.length, prefix.length + insert.length); });
    };
    const changeText = (content: string, caret: number) => {
        update({ content });
        const before = content.slice(0, caret);
        const match = before.match(/(?:^|\s)([@#])([\p{L}\p{N}_-]{0,40})$/u);
        setPicker(match ? { kind: match[1] as "@" | "#", query: match[2], start: caret - match[2].length - 1, end: caret } : null);
        setSelection(0);
        if (content.trim()) {
            if (Date.now() - lastTyping.current > 2100) { chat.signalTyping(true); lastTyping.current = Date.now(); }
            clearTimeout(typingStop.current);
            typingStop.current = setTimeout(() => chat.signalTyping(false), 3000);
        } else chat.signalTyping(false);
    };
    const submit = async () => {
        if (disabled || uploading || useChatUiStore.getState().busy[`${sessionKey}:send`] || chat.state !== "connected" || !(draft.content.trim() || draft.entities.length || draft.attachments.length)) return;
        setSending(true); setError(null); setPicker(null); chat.signalTyping(false);
        const body: ChatMessageBody = { content: draft.content.trim(), mentions: draft.mentions, entities: draft.entities.map(({ entity_type, entity_id }) => ({ entity_type, entity_id })), attachment_ids: draft.attachments.map((file) => file.id) };
        try {
            if (editing) {
                await chat.command("message.edit", { ...body, expected_revision: editing.revision }, editing.id);
                onCancelEdit();
            } else {
                // UUID и тело черновика сохраняются до ack — retry переживает даже reload.
                updateStored(draftKey, draft);
                await chat.send({ ...body, client_message_id: draft.clientMessageId, reply_to_message_id: draft.reply?.id ?? null }, draft);
                clearStored(draftKey, draft.clientMessageId);
            }
            input.current?.focus();
        } catch (failure) { setError((failure as Error).message); } finally { setSending(false); }
    };
    const keyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
        if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
        if (picker && choices.length) {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); setSelection((old) => (old + (event.key === "ArrowDown" ? 1 : -1) + choices.length) % choices.length); return; }
            if ((event.key === "Enter" && !isTouchChatInput()) || event.key === "Tab") { event.preventDefault(); choose(choices[Math.min(selection, choices.length - 1)]); return; }
        }
        if (event.key === "Escape") {
            if (picker) { event.preventDefault(); setPicker(null); }
            else if (editing) { event.preventDefault(); onCancelEdit(); }
            return;
        }
        if (shouldSubmitChat(event)) { event.preventDefault(); void submit(); }
    };
    const openPicker = (kind: "@" | "#") => { const caret = input.current?.selectionStart ?? draft.content.length; setPicker({ kind, query: "", start: caret, end: caret }); setSelection(0); input.current?.focus(); };

    return <div className="chat-composer relative mx-auto w-full shrink-0 max-w-[var(--chat-content-width,56rem)] px-3 pt-3 pb-3 sm:px-5" onDragOver={(event) => { if (!editing && event.dataTransfer.types.includes("Files")) event.preventDefault(); }} onDrop={(event) => { if (event.dataTransfer.files.length && !editing) { event.preventDefault(); void upload(event.dataTransfer.files); } }}>
        {picker && <div className="chat-picker material-glass absolute right-3 bottom-full left-3 z-20 mb-2 overflow-y-auto rounded-xl border border-line p-1.5 shadow-panel sm:right-5 sm:left-5" role="listbox" id="chat-picker" aria-label={picker.kind === "@" ? "Участники проекта" : "Объекты проекта"}>
            <div className="flex items-center gap-2 px-2.5 py-1"><p className="min-w-0 flex-1 text-[11px] text-secondary">{picker.kind === "@" ? "Упомянуть участника" : "Сослаться на объект"}<span className="hidden sm:inline"> · ↑ ↓ выбрать · Enter добавить</span></p><IconButton label="Закрыть выбор" onClick={() => setPicker(null)}><X size={14} /></IconButton></div>
            {choices.slice(0, 30).map((choice, index) => <button type="button" role="option" aria-selected={index === selection} id={`chat-choice-${index}`} key={"username" in choice ? choice.id : `${choice.entity_type}:${choice.entity_id}`} onMouseDown={(event) => event.preventDefault()} onClick={() => choose(choice)} className={cn("flex min-h-11 w-full items-center gap-2 rounded-md px-2.5 py-2.5 text-left text-xs transition-colors", index === selection ? "bg-accent/10 text-primary" : "text-secondary hover:bg-hover")}>
                {"username" in choice ? <><span className={cn("size-1.5 shrink-0 rounded-full", chat.online.includes(choice.id) ? "bg-success" : "bg-muted/40")} /><span className="min-w-0 flex-1 truncate">{choice.display_name}</span><span className="ml-auto max-w-[45%] truncate text-muted">@{choice.username}</span></> : <><Hash size={13} className="shrink-0 text-muted" /><span className="min-w-0 flex-1 truncate">{choice.title}</span><span className="max-w-[35%] truncate text-[10px] text-muted">{choice.subtitle ?? ({ TASK: "Задача", DOCUMENT: "Документ", RISK: "Риск", MILESTONE: "Веха", WBS_NODE: "Структура" }[choice.entity_type])}</span></>}
            </button>)}
            {!choices.length && <p className="px-2 py-3 text-xs text-muted">{entities.isFetching && picker.kind === "#" ? "Поиск…" : "Ничего не найдено"}</p>}
        </div>}
        <div className={cn("surface-card overflow-hidden rounded-xl border border-line shadow-card transition-colors", isForTim(draft.content) ? "focus-within:border-ai-blue/60" : "focus-within:border-accent/60")}>
            {(draft.reply || editing) && <div className="chat-reply-bar flex items-center gap-2 border-b border-line bg-surface px-4 py-2.5 text-xs text-secondary">
                <CornerUpLeft size={14} className="shrink-0 text-accent" />
                <span className="min-w-0 flex-1 truncate">{editing ? "Редактирование сообщения" : `Ответ ${draft.reply?.author_type === "AGENT" ? "Арису" : draft.reply ? chatAuthorName(draft.reply, "участнику") : "участнику"}: ${draft.reply?.content || "Вложение"}`}</span>
                <IconButton label={editing ? "Отменить редактирование" : "Убрать ответ"} size="sm" onClick={() => editing ? onCancelEdit() : update({ reply: null })}><X size={12} /></IconButton>
            </div>}
            {(draft.entities.length > 0 || draft.mentions.length > 0 || draft.attachments.length > 0) && <div aria-label="Вложения и ссылки сообщения" className="chat-draft-items flex flex-wrap gap-2 px-4 pt-3">
                {draft.mentions.map((id) => <span key={`mention-${id}`} className="chat-draft-chip flex items-center gap-1.5 rounded-md border border-accent/20 bg-accent/8 px-2 py-1 text-[11px] text-accent"><span className="truncate">@{chat.info?.members.find((user) => user.id === id)?.display_name ?? "Участник"}</span><button type="button" aria-label="Убрать упоминание" disabled={disabled} onClick={() => update({ mentions: draft.mentions.filter((value) => value !== id) })}><X size={11} /></button></span>)}
                {draft.entities.map((entity) => <span key={`${entity.entity_type}:${entity.entity_id}`} className="chat-draft-chip control-surface flex max-w-full items-center gap-1.5 rounded-md border border-line px-2 py-1 text-[11px] text-secondary"><Hash size={11} className="shrink-0" /><span className="max-w-60 truncate">{entity.title}</span><button type="button" aria-label={`Убрать ссылку ${entity.title}`} disabled={disabled} onClick={() => update({ entities: draft.entities.filter((ref) => ref !== entity) })}><X size={11} /></button></span>)}
                {draft.attachments.map((file) => <span key={file.id} className="chat-draft-chip control-surface flex max-w-full items-center gap-1.5 rounded-md border border-line px-2 py-1 text-[11px] text-secondary"><Paperclip size={11} className="shrink-0" /><span className="max-w-56 truncate">{file.original_name}</span>{!editing && <button type="button" aria-label={`Убрать файл ${file.original_name}`} disabled={disabled || uploading} onClick={() => void removeFile(file)}><X size={11} /></button>}</span>)}
            </div>}
            <textarea ref={input} id="project-chat-composer" aria-label={editing ? "Редактирование сообщения" : "Сообщение проекту"} aria-controls={picker ? "chat-picker" : undefined} aria-expanded={Boolean(picker)} aria-activedescendant={picker && choices.length ? `chat-choice-${selection}` : undefined} aria-autocomplete="list" role="combobox" aria-describedby={!editing && chat.canWrite ? "chat-composer-hint chat-composer-agent-hint" : "chat-composer-hint"} rows={2} maxLength={8000} value={draft.content} disabled={disabled} onChange={(event) => changeText(event.target.value, event.target.selectionStart)} onKeyDown={keyDown} onPaste={(event) => { if (event.clipboardData.files.length && !editing) { event.preventDefault(); void upload(event.clipboardData.files); } }} placeholder={chat.canWrite ? "Сообщение команде…  @ участник, # объект, Арис, …" : "Этот токен разрешает только чтение"} className="chat-input block min-h-24 w-full resize-none bg-transparent px-4 pt-4 pb-2 text-[14px] leading-relaxed text-primary outline-none placeholder:text-muted disabled:opacity-50" />
            {!editing && chat.canWrite && <p id="chat-composer-agent-hint" className={cn("chat-composer-hint px-4 pb-3 text-[11px] leading-relaxed", isForTim(draft.content) ? "text-ai-blue" : "text-muted")}>{isForTim(draft.content) ? "Арис — проектный менеджер — ответит здесь, в чате проекта" : TIM_CHAT_HINT}</p>}
            {draft.content.length > 7000 && <p className="px-3 pb-1 text-right font-mono text-[10px] text-muted sm:hidden">{draft.content.length}/8000</p>}
            <div className="chat-composer-toolbar flex items-center gap-0.5 border-t border-line-subtle bg-surface px-2 py-2 sm:px-3">
                {!editing && <><input ref={fileInput} type="file" multiple className="sr-only" tabIndex={-1} aria-label="Файлы сообщения" onChange={(event) => { if (event.target.files) void upload(event.target.files); event.target.value = ""; }} /><IconButton label="Прикрепить файлы" disabled={disabled || uploading || draft.attachments.length >= 5} onClick={() => fileInput.current?.click()}>{uploading ? <Loader2 size={15} className="animate-spin motion-reduce:animate-none" /> : <Paperclip size={15} />}</IconButton></>}
                <IconButton label="Упомянуть участника" disabled={disabled} onClick={() => openPicker("@")}><AtSign size={15} /></IconButton>
                <IconButton label="Добавить ссылку на объект" disabled={disabled} onClick={() => openPicker("#")}><Hash size={15} /></IconButton>
                <span id="chat-composer-hint" className="chat-keyboard-hint ml-2 hidden flex-1 text-[10px] text-muted sm:block">Enter — {editing ? "сохранить" : "отправить"} · Shift+Enter — новая строка</span>
                <span className="ml-auto mr-2 hidden font-mono text-[10px] text-muted sm:inline">{draft.content.length > 7000 ? `${draft.content.length}/8000` : ""}</span>
                <Button variant="primary" size="sm" className="ml-auto" disabled={disabled || uploading || chat.state !== "connected" || !(draft.content.trim() || draft.entities.length || draft.attachments.length)} icon={sending ? <Loader2 size={13} className="animate-spin motion-reduce:animate-none" /> : <Send size={13} />} onClick={() => void submit()}>{editing ? "Сохранить" : "Отправить"}</Button>
            </div>
        </div>
        {error && <p role="alert" className="mt-2 text-xs text-danger">{error}</p>}
        <div className="chat-composer-status mt-2 min-h-4 px-1 text-[10px] leading-relaxed text-muted" aria-live="off">{uploading ? "Загрузка файлов…" : chat.typing.length ? `${chat.typing.map((id) => chat.info?.members.find((user) => user.id === id)?.display_name).filter(Boolean).slice(0, 3).join(", ")} ${chat.typing.length === 1 ? "печатает" : "печатают"}…` : "Сообщения видны всем участникам проекта"}</div>
    </div>;
}
