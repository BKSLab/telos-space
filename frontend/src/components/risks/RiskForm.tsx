import { useId, useState, type FormEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { ProjectMember, Task } from "@/lib/types";
import {
    IMPACT_LABELS, PROBABILITY_LABELS, RISK_RATINGS, RISK_STATUS_LABELS, RISK_STRATEGY_LABELS,
    previewRiskLevel, riskFormError, riskInput, type RiskInput, type RiskRating, type RiskStatus, type RiskStrategy,
} from "@/lib/risks";
import { Field, Input, Select, Textarea } from "@/components/ui/Field";
import { ErrorMessage } from "@/components/ui/States";
import { Button } from "@/components/ui/Button";
import { RiskBadge } from "@/components/risks/RiskBadge";
import { RiskFieldAssistant } from "@/components/risks/RiskFieldAssistant";
import { UserSelect } from "@/components/users/UserSelect";

interface Props {
    projectId: number;
    initial?: Partial<RiskInput>;
    formId: string;
    isSaving: boolean;
    error?: string | null;
    onSave: (input: RiskInput) => void;
}

export function RiskForm({ projectId, initial, formId, isSaving, error, onSave }: Props) {
    const [draft, setDraft] = useState<RiskInput>(() => riskInput(initial));
    const [validation, setValidation] = useState<string | null>(null);
    const [taskSearch, setTaskSearch] = useState("");
    const searchId = useId();
    const members = useQuery({
        queryKey: queryKeys.projectMembers(projectId),
        queryFn: () => api.get<ProjectMember[]>(endpoints.projectMembers(projectId)),
    });
    const tasks = useQuery({
        queryKey: queryKeys.tasks(projectId),
        queryFn: () => api.get<Task[]>(endpoints.projectTasks(projectId)),
    });
    function change<K extends keyof RiskInput>(key: K, value: RiskInput[K]) {
        setDraft((current) => ({ ...current, [key]: value }));
        setValidation(null);
    }
    const search = taskSearch.trim().toLocaleLowerCase("ru-RU");
    const owner = members.data?.find(member => member.user.id === draft.owner_user_id)?.user;
    const visibleTasks = (tasks.data ?? []).filter((task) =>
        task.id === draft.task_id || `${task.key} ${task.title}`.toLocaleLowerCase("ru-RU").includes(search),
    );
    function submit(event: FormEvent) {
        event.preventDefault();
        const input = riskInput(draft);
        const message = riskFormError(input);
        setValidation(message);
        if (!message && !isSaving) onSave(input);
    }

    return (
        <form id={formId} onSubmit={submit} className="flex flex-col gap-4">
            <fieldset disabled={isSaving} className="flex min-w-0 flex-col gap-4">
                <legend className="sr-only">Поля риска</legend>
                <Field label="Название *">
                    {(id) => <Input id={id} autoFocus required maxLength={255} value={draft.title} onChange={(e) => change("title", e.target.value)} placeholder="Что может помешать проекту?" />}
                </Field>
                <Field label="Описание *" hint="Что может произойти, почему и чем это угрожает проекту. Поддерживается Markdown.">
                    {(id) => <Textarea id={id} required maxLength={20000} rows={3} value={draft.description} onChange={(e) => change("description", e.target.value)} />}
                </Field>
                <RiskFieldAssistant projectId={projectId} field="description" draft={draft} onAccept={(text) => change("description", text)} disabled={isSaving} />
                <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Вероятность *">
                        {(id) => <Select id={id} value={draft.probability} onChange={(e) => change("probability", e.target.value as RiskRating)}>{RISK_RATINGS.map((value) => <option key={value} value={value}>{PROBABILITY_LABELS[value]}</option>)}</Select>}
                    </Field>
                    <Field label="Влияние *">
                        {(id) => <Select id={id} value={draft.impact} onChange={(e) => change("impact", e.target.value as RiskRating)}>{RISK_RATINGS.map((value) => <option key={value} value={value}>{IMPACT_LABELS[value]}</option>)}</Select>}
                    </Field>
                </div>
                <div aria-live="polite" aria-atomic="true" className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <span className="text-[11px] text-muted">Уровень риска</span>
                    <RiskBadge level={previewRiskLevel(draft.probability, draft.impact)} />
                    <span className="text-[11px] text-muted">Рассчитывается автоматически</span>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Стратегия реагирования *">
                        {(id) => <Select id={id} value={draft.response_strategy} onChange={(e) => change("response_strategy", e.target.value as RiskStrategy)}>{Object.entries(RISK_STRATEGY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select>}
                    </Field>
                    <Field label="Статус">
                        {(id) => <Select id={id} value={draft.status} onChange={(e) => change("status", e.target.value as RiskStatus)}>{Object.entries(RISK_STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select>}
                    </Field>
                </div>
                <Field label="План митигации" hint="Что сделать заранее, чтобы снизить вероятность или влияние?">
                    {(id) => <Textarea id={id} rows={3} maxLength={20000} value={draft.mitigation_plan} onChange={(e) => change("mitigation_plan", e.target.value)} />}
                </Field>
                <RiskFieldAssistant projectId={projectId} field="mitigation_plan" draft={draft} onAccept={(text) => change("mitigation_plan", text)} disabled={isSaving} />
                <Field label="План реагирования" hint="Что делать, если риск потребует реакции или реализуется?">
                    {(id) => <Textarea id={id} rows={3} maxLength={20000} value={draft.response_plan} onChange={(e) => change("response_plan", e.target.value)} />}
                </Field>
                <RiskFieldAssistant projectId={projectId} field="response_plan" draft={draft} onAccept={(text) => change("response_plan", text)} disabled={isSaving} />
                <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Ответственный">
                        {(id) => <UserSelect id={id} label="Ответственный" users={(members.data ?? []).map(member => member.user)} selected={owner ? [owner] : []}
                            fallbackSelection={!owner && draft.owner_user_id ? { userId: draft.owner_user_id, name: `Участник #${draft.owner_user_id}` } : undefined}
                            emptyLabel="Не назначен" clearLabel="Не назначен" loading={members.isPending} disabled={isSaving || members.isPending || !!members.error}
                            describeUser={user => `@${user.username}`} onChange={users => change("owner_user_id", users[0]?.id ?? null)} />}
                    </Field>
                    <Field label="Дата контроля" hint="Дата следующего пересмотра риска.">
                        {(id) => <Input id={id} type="date" value={draft.review_date ?? ""} onChange={(e) => change("review_date", e.target.value || null)} />}
                    </Field>
                </div>
                {members.error && <ErrorMessage title="Не удалось загрузить команду" message={members.error.message} action={<Button onClick={() => void members.refetch()}>Повторить</Button>} />}
                <div className="flex flex-col gap-2">
                    <label htmlFor={searchId} className="text-[11px] font-medium text-secondary">Поиск задачи проекта</label>
                    <Input id={searchId} type="search" maxLength={255} placeholder="Ключ или название задачи" value={taskSearch} onChange={(e) => setTaskSearch(e.target.value)} />
                    <Field label="Связанная задача">
                        {(id) => <Select id={id} value={draft.task_id ?? ""} disabled={tasks.isPending || !!tasks.error} onChange={(e) => change("task_id", e.target.value ? Number(e.target.value) : null)}>
                            <option value="">{tasks.isPending ? "Загрузка задач…" : "Без связи с задачей"}</option>
                            {draft.task_id && !tasks.data?.some((item) => item.id === draft.task_id) && <option value={draft.task_id}>Задача #{draft.task_id}</option>}
                            {visibleTasks.map((task) => <option key={task.id} value={task.id}>{task.key} · {task.title}</option>)}
                        </Select>}
                    </Field>
                    {!tasks.isPending && !tasks.error && visibleTasks.length === 0 && <p role="status" className="text-[11px] text-muted">{search ? "Задачи не найдены." : "В проекте пока нет задач. Риск можно создать без связи."}</p>}
                </div>
                {tasks.error && <ErrorMessage title="Не удалось загрузить задачи" message={tasks.error.message} action={<Button onClick={() => void tasks.refetch()}>Повторить</Button>} />}
            </fieldset>
            {(validation || error) && <p role="alert" className="text-[12px] text-danger">{validation || error}</p>}
        </form>
    );
}
