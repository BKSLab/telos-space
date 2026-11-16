import { useId, useState, type ReactNode } from "react";
import { CalendarClock } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Field, Input, Textarea } from "@/components/ui/Field";
import { Modal } from "@/components/ui/Modal";
import { ErrorMessage } from "@/components/ui/States";
import { formatFullDate } from "@/lib/dates";
import { requiresTaskDateChangeReason, type TaskDateValues } from "@/lib/taskDates";
import type { Task } from "@/lib/types";

export interface TaskDatesSave extends TaskDateValues {
    dateChangeReason?: string;
}

interface TaskDatesDialogProps {
    task: Pick<Task, "id" | "key" | "title" | "start_date" | "due_date">
        & Partial<Pick<Task, "baseline_start_date" | "baseline_due_date">>;
    initialDates?: TaskDateValues;
    isSaving: boolean;
    error?: string | null;
    onClose: () => void;
    onSave: (value: TaskDatesSave) => void;
    onFixBaseline?: () => void;
    onDatesChange?: (dates: TaskDateValues) => void;
    requireReason?: boolean;
    saveDisabled?: boolean;
    saveLabel?: string;
    children?: ReactNode;
}

/** Общий черновик сроков: даты и причина отправляются только по явному сохранению. */
export function TaskDatesDialog({
    task,
    initialDates,
    isSaving,
    error,
    onClose,
    onSave,
    onFixBaseline,
    onDatesChange,
    requireReason,
    saveDisabled = false,
    saveLabel = "Сохранить сроки",
    children,
}: TaskDatesDialogProps) {
    const [dates, setDates] = useState<TaskDateValues>(() => initialDates ?? {
        startDate: task.start_date,
        dueDate: task.due_date,
    });
    const [reason, setReason] = useState("");
    const [focusReason] = useState(() => Boolean(initialDates
        && (requireReason ?? requiresTaskDateChangeReason(task, initialDates))));
    const reasonHintId = useId();
    const rangeErrorId = useId();
    const reasonRequired = requireReason ?? requiresTaskDateChangeReason(task, dates);
    const isReverse = Boolean(dates.startDate && dates.dueDate && dates.startDate > dates.dueDate);
    const isUnchanged = task.start_date === dates.startDate && task.due_date === dates.dueDate;

    function changeDates(next: TaskDateValues) {
        setDates(next);
        onDatesChange?.(next);
    }

    return (
        <Modal
            isOpen
            title="Изменить сроки"
            description={`${task.key} · ${task.title}`}
            size="md"
            isDismissable={!isSaving}
            onOpenChange={open => { if (!open && !isSaving) onClose(); }}
            footer={(
                <>
                    {onFixBaseline && <Button variant="secondary" disabled={isSaving} onClick={onFixBaseline}>Зафиксировать baseline</Button>}
                    <Button variant="ghost" disabled={isSaving} onClick={onClose}>Отмена</Button>
                    <Button
                        variant="primary"
                        icon={<CalendarClock size={14} aria-hidden="true" />}
                        disabled={isSaving || saveDisabled || isUnchanged || isReverse || (reasonRequired && !reason.trim())}
                        onClick={() => onSave({ ...dates, ...(reasonRequired && { dateChangeReason: reason.trim() }) })}
                    >
                        {isSaving ? "Сохранение…" : saveLabel}
                    </Button>
                </>
            )}
        >
            <div className="flex flex-col gap-5">
                <div className="grid gap-4 sm:grid-cols-2">
                    <Field label="Начало" hint={`Сейчас: ${task.start_date ? formatFullDate(task.start_date) : "не задано"}`}>
                        {id => <Input id={id} type="date" value={dates.startDate ?? ""} disabled={isSaving} autoFocus={!focusReason}
                            aria-invalid={isReverse || undefined} aria-describedby={isReverse ? rangeErrorId : undefined}
                            onChange={event => changeDates({ ...dates, startDate: event.target.value || null })} />}
                    </Field>
                    <Field label="Завершение" hint={`Сейчас: ${task.due_date ? formatFullDate(task.due_date) : "не задано"}`}>
                        {id => <Input id={id} type="date" value={dates.dueDate ?? ""} disabled={isSaving}
                            aria-invalid={isReverse || undefined} aria-describedby={isReverse ? rangeErrorId : undefined}
                            onChange={event => changeDates({ ...dates, dueDate: event.target.value || null })} />}
                    </Field>
                </div>
                {isReverse && <p id={rangeErrorId} role="alert" className="text-xs text-danger">Завершение не может быть раньше начала.</p>}
                {reasonRequired && (
                    <Field label="Причина изменения (обязательно)">
                        {id => <>
                            <Textarea id={id} required rows={3} maxLength={2000} value={reason} disabled={isSaving} autoFocus={focusReason}
                                placeholder="Например, ждём согласование макета от заказчика"
                                aria-describedby={reasonHintId} onChange={event => setReason(event.target.value)} />
                            <p id={reasonHintId} className="text-xs text-muted">Сохраним причину и изменение дат одной записью в истории задачи.</p>
                        </>}
                    </Field>
                )}
                {children}
                {(task.baseline_start_date || task.baseline_due_date) && (
                    <p className="text-xs text-muted">Текущий baseline: {task.baseline_start_date ?? "—"} — {task.baseline_due_date ?? "—"}</p>
                )}
                {error && <ErrorMessage message={error} />}
            </div>
        </Modal>
    );
}
