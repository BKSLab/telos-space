import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Plus, Trash2, WandSparkles } from "lucide-react";
import { api, endpoints } from "@/lib/api";
import { checklistInput, isChecklistValid, moveChecklistItem, newChecklistItem } from "@/lib/checklists";
import type { ChecklistSuggestion, TaskChecklist } from "@/lib/checklists";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { ErrorMessage } from "@/components/ui/States";
import { Modal } from "@/components/ui/Modal";
import { AiOperationStatus } from "@/components/ui/AiOperationStatus";

interface EditorProps {
    value: TaskChecklist;
    onChange: (value: TaskChecklist) => void;
    disabled?: boolean;
}

function ChecklistEditor({ value, onChange, disabled }: EditorProps) {
    return <div className="flex min-w-0 flex-col gap-2">
        <Input aria-label="Название чек-листа" maxLength={120} value={value.title} disabled={disabled}
            onChange={event => onChange({ ...value, title: event.target.value })} />
        <ol className="flex min-w-0 flex-col gap-2">
            {value.items.map((item, index) => <li key={item.id} className="flex min-w-0 flex-wrap items-center gap-1.5">
                <input type="checkbox" aria-label={`Выполнен пункт ${index + 1}`} checked={item.is_completed} disabled={disabled}
                    className="size-4 shrink-0 accent-[var(--color-accent)]"
                    onChange={event => onChange({ ...value, items: value.items.map(row => row.id === item.id ? { ...row, is_completed: event.target.checked } : row) })} />
                <Input aria-label={`Пункт ${index + 1}`} maxLength={500} value={item.text} disabled={disabled}
                    placeholder="Что нужно сделать или проверить" className="min-w-0 flex-1 basis-40"
                    onChange={event => onChange({ ...value, items: value.items.map(row => row.id === item.id ? { ...row, text: event.target.value } : row) })} />
                <div className="ml-auto flex gap-0.5">
                    <Button size="sm" aria-label={`Поднять пункт ${index + 1}`} disabled={disabled || index === 0}
                        onClick={() => onChange(moveChecklistItem(value, index, -1))}><ArrowUp size={13} /></Button>
                    <Button size="sm" aria-label={`Опустить пункт ${index + 1}`} disabled={disabled || index === value.items.length - 1}
                        onClick={() => onChange(moveChecklistItem(value, index, 1))}><ArrowDown size={13} /></Button>
                    <Button size="sm" aria-label={`Удалить пункт ${index + 1}`} disabled={disabled}
                        onClick={() => onChange({ ...value, items: value.items.filter(row => row.id !== item.id) })}><Trash2 size={13} /></Button>
                </div>
            </li>)}
        </ol>
        <Button size="sm" disabled={disabled || value.items.length >= 100}
            onClick={() => onChange({ ...value, items: [...value.items, newChecklistItem()] })}>
            <Plus size={13} />Добавить пункт
        </Button>
        {!isChecklistValid(value) && <p role="status" className="text-xs text-warning">Заполните название и текст каждого пункта.</p>}
    </div>;
}

interface TaskChecklistFieldProps {
    projectId: number | null;
    taskId?: number;
    title: string;
    description: string;
    documentRefs?: string[];
    files?: File[];
    value: TaskChecklist | null;
    onChange: (value: TaskChecklist | null) => void;
    onAccept?: (value: TaskChecklist, revision?: number) => Promise<void>;
    revision?: number;
    onRemove?: () => Promise<void>;
    editing?: boolean;
    onEdit?: () => void;
    disabled?: boolean;
    onBusyChange?: (busy: boolean) => void;
}

export function TaskChecklistField({ projectId, taskId, title, description, documentRefs = [], files = [],
    value, onChange, onAccept, onRemove, revision, editing = true, onEdit, disabled = false, onBusyChange }: TaskChecklistFieldProps) {
    const [proposal, setProposal] = useState<(ChecklistSuggestion & { revision?: number }) | null>(null);
    const [confirmDelete, setConfirmDelete] = useState(false);
    const [actionError, setActionError] = useState<string | null>(null);
    const [accepting, setAccepting] = useState(false);
    const generate = useMutation({
        mutationFn: async () => {
            const body = new FormData();
            body.append("payload", JSON.stringify({ title: title.trim(), description_md: description, task_id: taskId ?? null,
                document_refs: documentRefs, ...(isChecklistValid(value) ? { checklist: checklistInput(value) } : {}) }));
            for (const file of files) body.append("files", file);
            const result = await api.postForm<ChecklistSuggestion>(endpoints.taskChecklistSuggestion(projectId!), body);
            return { ...result, revision };
        },
        onSuccess: result => { setProposal(result); setActionError(null); },
    });
    useEffect(() => { onBusyChange?.(generate.isPending || accepting); }, [generate.isPending, accepting, onBusyChange]);
    const busy = disabled || generate.isPending || accepting;
    const completed = value?.items.filter(item => item.is_completed).length ?? 0;

    async function accept() {
        if (!proposal || !isChecklistValid(proposal.checklist)) return;
        setAccepting(true);
        setActionError(null);
        try {
            const next = checklistInput(proposal.checklist)!;
            if (onAccept) await onAccept(next, proposal.revision);
            else onChange(next);
            setProposal(null);
        } catch (error) { setActionError((error as Error).message); }
        finally { setAccepting(false); }
    }

    async function remove() {
        setAccepting(true);
        setActionError(null);
        try {
            if (onRemove) await onRemove();
            else onChange(null);
            setConfirmDelete(false);
        } catch (error) { setActionError((error as Error).message); }
        finally { setAccepting(false); }
    }

    return <section aria-label="Чек-лист задачи" className="flex min-w-0 flex-col gap-3 rounded-[var(--radius-card)] border border-line-subtle p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
            <h4 className="text-sm font-medium">Чек-лист {value && <span className="ml-1 font-mono text-xs text-muted">{completed}/{value.items.length}</span>}</h4>
            <div className="flex flex-wrap gap-1.5">
                {!value && <Button size="sm" disabled={busy} onClick={() => onChange({ title: "Чек-лист", items: [newChecklistItem()] })}>Добавить чек-лист</Button>}
                {value && !editing && <Button size="sm" disabled={busy} onClick={onEdit}>Редактировать чек-лист</Button>}
                <Button size="sm" variant="ai" disabled={projectId === null || busy || !title.trim()} onClick={() => generate.mutate()}>
                    <WandSparkles size={13} />{generate.isPending ? "Формирование…" : "Сформировать чек-лист"}
                </Button>
                {value && <Button size="sm" aria-label="Удалить чек-лист" disabled={busy} onClick={() => setConfirmDelete(true)}><Trash2 size={13} /></Button>}
            </div>
        </div>
        {generate.isPending && <AiOperationStatus message="Арис готовит чек-лист задачи…"
            description="Предложение появится здесь. Вы сможете проверить его перед применением." />}
        {generate.error && <ErrorMessage message={(generate.error as Error).message} />}
        {actionError && <ErrorMessage message={actionError} />}
        {value && (editing ? <ChecklistEditor value={value} onChange={onChange} disabled={busy} /> : <div className="flex flex-col gap-2">
            {value.title !== "Чек-лист" && <p className="break-words text-sm text-secondary">{value.title}</p>}
            {value.items.length === 0 && <p className="text-xs text-muted">Пунктов пока нет.</p>}
            {value.items.map(item => <label key={item.id} className="flex min-w-0 items-start gap-2 text-sm">
                <input type="checkbox" checked={item.is_completed} disabled={busy} className="mt-0.5 size-4 shrink-0 accent-[var(--color-accent)]"
                    onChange={event => onChange({ ...value, items: value.items.map(row => row.id === item.id ? { ...row, is_completed: event.target.checked } : row) })} />
                <span className={`min-w-0 break-words ${item.is_completed ? "text-muted line-through" : "text-secondary"}`}>{item.text}</span>
            </label>)}
        </div>)}
        {proposal && <section aria-label="Предложение AI" className="flex flex-col gap-3 rounded-md border border-accent-border bg-accent-soft p-3">
            <p className="text-xs text-secondary">Предложение ещё не сохранено. Отредактируйте пункты или примите как есть.</p>
            {value && <p className="text-xs text-muted">Принятие заменит текущий чек-лист.</p>}
            {proposal.warnings.length > 0 && <ul className="list-inside list-disc text-xs text-warning">{proposal.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}
            <ChecklistEditor value={proposal.checklist} onChange={checklist => setProposal({ ...proposal, checklist })} disabled={busy} />
            <div className="flex flex-wrap justify-end gap-2">
                <Button size="sm" disabled={busy} onClick={() => setProposal(null)}>Отклонить предложение</Button>
                <Button size="sm" variant="primary" disabled={busy || !isChecklistValid(proposal.checklist)} onClick={() => void accept()}>
                    {value ? "Заменить чек-лист" : "Принять чек-лист"}
                </Button>
            </div>
        </section>}
        <Modal title="Удалить чек-лист?" description="Будут удалены все его пункты и отметки выполнения."
            isOpen={confirmDelete} onOpenChange={open => { if (!accepting) setConfirmDelete(open); }} isDismissable={!accepting}
            footer={<><Button disabled={accepting} onClick={() => setConfirmDelete(false)}>Отмена</Button>
                <Button variant="destructive" disabled={accepting} onClick={() => void remove()}>Удалить чек-лист</Button></>}>
            {actionError && <ErrorMessage message={actionError} />}
        </Modal>
    </section>;
}
