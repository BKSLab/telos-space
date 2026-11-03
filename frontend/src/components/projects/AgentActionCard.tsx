import { CheckCircle2, CircleAlert, Clock3, XCircle } from "lucide-react";
import type { AgentToolRun, KnowledgeSource, Project } from "@/lib/types";
import { actionSources } from "@/lib/agentCards";
import { AgentEntityCard } from "@/components/agent/AgentEntityCard";
import { Button } from "@/components/ui/Button";

const LABELS: Record<string, string> = {
    task_id: "Задача", task_key: "Ключ задачи", title: "Название", name: "Название", body_md: "Текст",
    description_md: "Описание", stage_id: "Стадия", position: "Позиция", changes: "Изменения",
    user_id: "Участник", username: "Логин", comment_id: "Комментарий", document_id: "Документ",
    attachment_id: "Файл", sticker_id: "Стикер", node_id: "Раздел ИСР", milestone_id: "Веха",
    dependency_id: "Зависимость", risk_id: "Риск", revision: "Версия", expected_updated_at: "Прочитанная версия",
    expected_body_md: "Прежний текст", start_date: "Начало", due_date: "Срок", planned_start: "Начало",
    planned_end: "Окончание", expected_version: "Прочитанная версия", reason: "Причина",
    checklist: "Чек-лист", checklist_revision: "Версия чек-листа", parent_id: "Родительский раздел",
    // Объекты из снимка «что затронет» (preview) — показываются в окне подтверждения по ссылке.
    task: "Задача", tasks: "Задачи", task_count: "Сколько задач", document: "Документ", comment: "Комментарий",
    sticker: "Стикер", member: "Участник", stage: "Стадия", milestone: "Веха", dependency: "Зависимость",
    risk: "Риск", attachment: "Файл", key: "Ключ", body: "Текст", user: "Сотрудник", full_name: "ФИО",
    login: "Логин", role: "Роль", status: "Статус",
};

/** Служебные поля защиты от одновременной правки — человеку при решении не нужны. */
const HIDDEN = new Set(["revision", "expected_updated_at", "expected_version", "checklist_revision", "position", "expected_body_md",
    // Служебные поля снимка объекта: номер записи и время правки человеку ничего не говорят.
    "id", "updated_at", "created_at"]);

/** Параметры действия человеческими подписями; служебные поля скрыты. Общий с окном подтверждения по ссылке. */
export function Parameters({ value }: { value: unknown }) {
    if (value == null) return <span>Не задано</span>;
    if (Array.isArray(value)) return <ol className="list-decimal space-y-1 pl-4">{value.map((item, index) => <li key={index}><Parameters value={item} /></li>)}</ol>;
    if (typeof value === "object") return <dl className="space-y-1">{Object.entries(value).filter(([key]) => !HIDDEN.has(key)).map(([key, item]) => (
        <div key={key}><dt className="font-medium" title={LABELS[key] ? undefined : `Поле «${key}»`}>{LABELS[key] ?? "Другое поле"}</dt><dd className="whitespace-pre-wrap break-words pl-2"><Parameters value={item} /></dd></div>
    ))}</dl>;
    return <span>{String(value)}</span>;
}

export interface ActionDecisionRules {
    /** Можно ли сейчас решать (ответ дописан, нет другого решения в полёте). */
    enabled: boolean;
    /** Показывать «Подтвердить» (в чате проекта — у кого есть права записи). */
    canApprove?: boolean;
    /** Показывать «Отклонить» (в чате проекта — ещё и автор просьбы). */
    canReject?: boolean;
    /** Почему кнопки неактивны — подсказка при наведении. */
    disabledHint?: string;
    /** Что будет после «Подтвердить» — подсказка при наведении. */
    approveHint?: string;
    /** Пояснение под карточкой, пока действие ждёт решения. */
    note?: string;
}

const DEFAULT_DISABLED_HINT = "Решить можно только по последнему ответу Ариса. Попросите его предложить действие ещё раз";

export function AgentActionCard({ action, project, sources, pending, onDecision, rules, enabled: legacyEnabled }: {
    action: AgentToolRun;
    project: Pick<Project, "id" | "key"> | null;
    sources: KnowledgeSource[];
    pending: boolean;
    onDecision: (decision: "approve" | "reject") => void;
    rules?: ActionDecisionRules;
    enabled?: boolean;
}) {
    const decisionRules = rules ?? { enabled: legacyEnabled ?? false };
    const { enabled, canApprove = true, canReject = true } = decisionRules;
    const disabledHint = decisionRules.disabledHint ?? DEFAULT_DISABLED_HINT;
    const status = { pending: "Ожидает решения", completed: "Выполнено", rejected: "Отклонено", failed: "Не выполнено" }[action.status];
    const Icon = { pending: Clock3, completed: CheckCircle2, rejected: XCircle, failed: CircleAlert }[action.status];
    const cards = actionSources(action);
    const decidedBy = action.result.decided_by && typeof action.result.decided_by === "object" ? (action.result.decided_by as { name?: unknown }).name : null;
    return <section aria-label={`Действие: ${action.title}`} className="mt-3 rounded-md border border-line bg-surface/80 p-3 text-[12px]">
        <p className="font-medium text-primary">{action.title}</p>
        <p role="status" className={`mt-1 flex items-center gap-1.5 ${action.status === "completed" ? "text-success" : action.status === "failed" ? "text-danger" : "text-muted"}`}><Icon size={13} />{status}{typeof decidedBy === "string" && decidedBy && action.status !== "pending" ? ` · решение: ${decidedBy}` : ""}</p>
        {cards.length > 0 && <div className="mt-3 space-y-2">{cards.map((source) => <AgentEntityCard key={source.source_id} source={action.status === "completed" ? sources.find((item) => item.source_id === source.source_id) ?? source : source} project={project} />)}</div>}
        {!cards.length && action.result.preview != null && action.status === "pending" && <div className="mt-2"><Parameters value={action.result.preview} /></div>}
        {typeof action.result.message === "string" && action.status !== "pending" && <p className="mt-2 text-secondary">{action.result.message}</p>}
        {typeof action.result.error === "string" && <p className="mt-2 text-danger">{action.result.error}</p>}
        <details open={action.status === "pending"} className="mt-2">
            <summary className="cursor-pointer text-secondary">Параметры действия</summary>
            <div className="mt-2 max-h-64 overflow-y-auto"><Parameters value={action.arguments} /></div>
        </details>
        {action.status === "pending" && <p className="mt-2 text-[11px] text-muted">{decisionRules.note ?? "Арис предлагает изменение. Оно выполнится только после подтверждения; отменить потом можно только вручную."}</p>}
        {action.status === "pending" && (canApprove || canReject) && <div className="mt-3 flex gap-2">
            {canApprove && <Button size="sm" variant="primary" disabled={!enabled || pending} onClick={() => onDecision("approve")} title={enabled ? decisionRules.approveHint ?? "Выполнить изменение сейчас. Отменить можно только вручную" : disabledHint}>Подтвердить</Button>}
            {canReject && <Button size="sm" disabled={!enabled || pending} onClick={() => onDecision("reject")} title={enabled ? "Не выполнять — данные не изменятся" : disabledHint}>Отклонить</Button>}
        </div>}
    </section>;
}
