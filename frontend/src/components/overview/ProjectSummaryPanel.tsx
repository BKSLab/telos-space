import { UserIdentity } from "@/components/users/UserIdentity";
import type { CSSProperties, ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, ArrowUpRight, Ban, BookOpen, CalendarClock, ChevronDown, CircleCheckBig, Clock3, Flame, Flag, History, Info, Lightbulb, ListTree, RefreshCw, ShieldAlert, Sparkles, Users } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import { formatDateTime, formatFullDate } from "@/lib/dates";
import { reportFreshness } from "@/lib/overview";
import type { AnalyticsHealth, AnalyticsReport, AnalyticsSignals } from "@/lib/types";
import { ANALYTICS_HEALTH_LABELS, ANALYTICS_HORIZON_LABELS } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { ErrorMessage, Skeleton } from "@/components/ui/States";
import { AiOperationStatus } from "@/components/ui/AiOperationStatus";
import { FindingCard, ProgressCard, RecommendationCard } from "@/components/dashboard/AnalyticsCards";
import "./ProjectSummaryPanel.css";

const HEALTH_ICONS: Record<AnalyticsHealth, typeof Activity> = {
    STABLE: CircleCheckBig, WATCH: Activity, RISK: ShieldAlert, CRITICAL: Flame,
};

const SIGNAL_ROWS: { key: keyof AnalyticsSignals; label: string; icon: typeof Activity; tone: "danger" | "warning" | "neutral" }[] = [
    { key: "high_risks", label: "высоких рисков", icon: ShieldAlert, tone: "danger" },
    { key: "occurred_risks", label: "реализовавшихся рисков", icon: Flame, tone: "danger" },
    { key: "risks_review_overdue", label: "рисков с просроченным контролем", icon: Clock3, tone: "warning" },
    { key: "risks_without_owner", label: "рисков без ответственного", icon: Users, tone: "warning" },
    { key: "risks_without_mitigation", label: "рисков без митигации", icon: ShieldAlert, tone: "warning" },
    { key: "overdue_tasks", label: "просроченных задач", icon: CalendarClock, tone: "danger" },
    { key: "milestones_at_risk", label: "вех под угрозой", icon: Flag, tone: "warning" },
    { key: "due_soon_tasks", label: "задач со сроком на неделе", icon: CalendarClock, tone: "neutral" },
    { key: "blocked_tasks", label: "заблокированных задач", icon: Ban, tone: "danger" },
    { key: "stale_tasks", label: "задач без движения 2 недели", icon: Clock3, tone: "warning" },
    { key: "no_due_date_tasks", label: "задач без срока", icon: CalendarClock, tone: "warning" },
    { key: "unassigned_tasks", label: "задач без исполнителя", icon: Users, tone: "warning" },
    { key: "unplaced_tasks", label: "задач вне разделов ИСР", icon: ListTree, tone: "neutral" },
];

interface ProjectSummaryPanelProps {
    projectId?: number | null;
    dataUpdatedAt?: string | null;
    blockedReason?: string;
    onOpenTask: (taskId: number) => void;
}

/** Сохранённая оценка Ариса. Текущие показатели и факты страницы живут отдельно. */
export function ProjectSummaryPanel({ projectId = null, dataUpdatedAt = null, blockedReason, onOpenTask }: ProjectSummaryPanelProps) {
    const queryClient = useQueryClient();
    const reportQuery = useQuery({
        queryKey: queryKeys.dashboardAnalytics(projectId),
        queryFn: () => api.get<AnalyticsReport | null>(endpoints.dashboardAnalytics(projectId)),
    });
    const generate = useMutation({
        mutationFn: () => api.post<AnalyticsReport>(endpoints.dashboardAnalytics(), { project_id: projectId }),
        onSuccess: report => queryClient.setQueryData(queryKeys.dashboardAnalytics(projectId), report),
    });
    const report = reportQuery.data ?? null;
    const HealthIcon = report ? HEALTH_ICONS[report.health] : null;
    const freshness = reportFreshness(report?.created_at, dataUpdatedAt);

    return <section aria-label="Вывод Ариса" className="aris-summary-panel" data-freshness={freshness}>
        <header className="aris-summary-header">
            <span className="aris-summary-mark"><Sparkles size={21} aria-hidden="true" /></span>
            <div className="min-w-0 flex-1">
                <h2>Вывод Ариса</h2>
                <p>{projectId ? "Оценка проекта на момент разбора" : "Оценка активных проектов и разовых задач"}</p>
            </div>
            <Button variant="ai" size="lg" onClick={() => generate.mutate()} disabled={generate.isPending || Boolean(blockedReason)} title={blockedReason}
                icon={generate.isPending ? <RefreshCw size={15} className="animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <Sparkles size={15} aria-hidden="true" />}>
                {generate.isPending ? "Разбираю…" : report ? "Обновить разбор" : "Разобрать"}
            </Button>
        </header>

        {blockedReason && <p className="aris-summary-message text-muted">{blockedReason}</p>}
        {reportQuery.error && <div className="aris-summary-message"><ErrorMessage title="Разбор недоступен" message={(reportQuery.error as Error).message}
            action={<Button size="sm" onClick={() => void reportQuery.refetch()}>Повторить загрузку</Button>} /></div>}
        {generate.error && <div className="aris-summary-message"><ErrorMessage title="Разбор не сформирован" message={(generate.error as Error).message} /></div>}
        {generate.isPending && <div className="aris-summary-pending">
            <AiOperationStatus message={projectId ? "Арис готовит разбор проекта…" : "Арис готовит разбор портфеля…"}
                description={`Сопоставляет задачи, сроки, риски и обсуждения. ${report ? "Ниже сохранён предыдущий разбор." : "Результат появится здесь."}`} />
        </div>}

        {reportQuery.isPending ? <div role="status" aria-label="Загрузка разбора" className="flex flex-col gap-3 border-t border-line-subtle p-5"><Skeleton className="h-5 w-2/3" /><Skeleton className="h-4 w-1/2" /></div> : report ? <>
            <div className="aris-summary-preview">
                <div className="aris-summary-assessment">
                    <div className="aris-summary-statusline">
                        {HealthIcon && <span className="aris-health" data-health={report.health}><HealthIcon size={14} aria-hidden="true" />Оценка разбора: {ANALYTICS_HEALTH_LABELS[report.health]}</span>}
                        <time dateTime={report.created_at} title={formatDateTime(report.created_at)}><History size={12} aria-hidden="true" />{formatFullDate(report.created_at)}</time>
                    </div>
                    <p className="aris-summary-headline">{report.headline}</p>
                    <p className="aris-summary-note">{report.health_note}</p>
                </div>
                {report.recommendations.length > 0 && <section className="aris-summary-recommendations" aria-label="Рекомендации из разбора">
                    <h3><Lightbulb size={14} aria-hidden="true" />Рекомендации из разбора</h3>
                    <ul>{report.recommendations.slice(0, 2).map((item, index) => <li key={`${item.title}-${index}`}>
                        <ArrowUpRight size={14} aria-hidden="true" />
                        <span>{item.title}</span><span className="aris-preview-horizon">{ANALYTICS_HORIZON_LABELS[item.horizon]}</span>
                    </li>)}</ul>
                </section>}
            </div>
            {freshness === "stale" && <p className="aris-summary-stale"><History size={13} aria-hidden="true" />Данные менялись после разбора. Обновите его, чтобы получить новую оценку.</p>}
            <details key={report.id} className="aris-summary-details">
                <summary><span>Полный разбор · {formatFullDate(report.created_at)}</span><ChevronDown size={17} aria-hidden="true" /></summary>
                <PulseDetail report={report} onOpenTask={onOpenTask} />
            </details>
        </> : !reportQuery.error && !generate.isPending && <p className="aris-summary-empty">{projectId ? "Попросите Ариса оценить ход работ, риски и следующие шаги по проекту." : "Попросите Ариса сопоставить активные проекты и разовые задачи и подсказать, с чего начать."}</p>}
    </section>;
}

function SavedSignals({ report }: { report: AnalyticsReport }) {
    const signals = SIGNAL_ROWS.flatMap(row => {
        const count = report.signals[row.key];
        return typeof count === "number" && count > 0 ? [{ ...row, count }] : [];
    });
    const largest = Math.max(...signals.map(row => row.count), 1);
    const { total_tasks: total, done_tasks: done } = report.signals;
    const circumference = 2 * Math.PI * 25;
    const share = total > 0 ? Math.max(0, Math.min(done / total, 1)) : 0;
    return <section className="aris-report-snapshot" aria-label="Сохранённые показатели разбора">
        <div className="aris-snapshot-completion">
            <svg viewBox="0 0 64 64" role="img" aria-label={total > 0 ? `Сохранённый снимок: завершено ${done} из ${total} задач` : "Сохранённый снимок: задач не было"}>
                <circle className="aris-snapshot-track" cx="32" cy="32" r="25" />
                {share > 0 && <circle className="aris-snapshot-value" cx="32" cy="32" r="25" strokeDasharray={`${share * circumference} ${circumference}`} transform="rotate(-90 32 32)" />}
                <text x="32" y="36" textAnchor="middle">{total > 0 ? done : "—"}</text>
            </svg>
            <div><span className="aris-report-eyebrow">Сохранённый снимок</span><p>{total > 0 ? `${done} из ${total} задач завершено` : "На дату разбора задач не было"}</p>
                <time dateTime={report.created_at}>{formatDateTime(report.created_at)}</time></div>
        </div>
        <div className="aris-snapshot-signals">
            <h3>Сигналы на момент разбора</h3>
            {signals.length > 0 ? <>
                <ul>{signals.map(({ key, label, icon: Icon, tone, count }) => <li key={key} data-tone={tone}>
                    <Icon size={14} aria-hidden="true" /><span>{label}</span><strong>{count}</strong>
                    <span className="aris-signal-track" aria-hidden="true"><span style={{ "--signal-width": `${count / largest * 100}%` } as CSSProperties} /></span>
                </li>)}</ul>
                <p>Сохранённые количества; сигналы могут пересекаться.</p>
            </> : <p>В сохранённом снимке нет ненулевых сигналов.</p>}
        </div>
    </section>;
}

function PulseDetail({ report, onOpenTask }: { report: AnalyticsReport; onOpenTask: (taskId: number) => void }) {
    const currentProjectKey = report.scope === "PROJECT" ? report.project_key : null;
    return <div className="aris-report-detail">
        <SavedSignals report={report} />
        <div className="aris-report-editorial">
            <div className="aris-report-observations">
                <ReportGroup title="Риски и препятствия" icon={<ShieldAlert size={17} aria-hidden="true" />} count={report.findings.length} tone="warning" empty="Проблем, требующих вмешательства, в разборе не найдено.">
                    {report.findings.map((finding, index) => <FindingCard key={`${finding.title}-${index}`} finding={finding} currentProjectKey={currentProjectKey} onOpenTask={onOpenTask} />)}
                </ReportGroup>
                <ReportGroup title="Что сделано" icon={<CircleCheckBig size={17} aria-hidden="true" />} count={report.progress.length} tone="success" empty="Заметных результатов в разборе не отмечено.">
                    {report.progress.map((progress, index) => <ProgressCard key={`${progress.title}-${index}`} progress={progress} currentProjectKey={currentProjectKey} onOpenTask={onOpenTask} />)}
                </ReportGroup>
            </div>
            <ReportGroup title="Рекомендации" icon={<Lightbulb size={17} aria-hidden="true" />} count={report.recommendations.length} tone="accent" empty="Дополнительных организационных действий в разборе нет.">
                {report.recommendations.map((recommendation, index) => <RecommendationCard key={`${recommendation.title}-${index}`} recommendation={recommendation} currentProjectKey={currentProjectKey} onOpenTask={onOpenTask} />)}
            </ReportGroup>
        </div>
        <PulseFooter report={report} />
    </div>;
}

function ReportGroup({ title, icon, count, tone, empty, children }: { title: string; icon: ReactNode; count: number; tone: string; empty: string; children: ReactNode }) {
    return <section className="aris-report-group" aria-label={title} data-tone={tone}>
        <h3>{icon}<span>{title}</span><span className="aris-group-count">{count}</span></h3>
        {count === 0 ? <p className="aris-report-group-empty">{empty}</p> : <div className="aris-report-items">{children}</div>}
    </section>;
}

const CONTEXT_LABELS: Record<string, string> = {
    tasks: "Задач", comments: "Комментариев", documents: "Документов", document_links: "Связей документов с задачами",
    stickers: "Стикеров", sticker_links: "Связей стикеров с задачами", wbs_nodes: "Разделов ИСР", milestones: "Вех",
    activity: "Событий истории", risks: "Рисков", stages: "Стадий", members: "Участников команды", participants: "Ролевых назначений",
    attachments: "Вложений", dependencies: "Зависимостей задач", checklists: "Чек-листов", checklist_items: "Пунктов чек-листов", outside_tasks: "Разовых задач",
};

function PulseFooter({ report }: { report: AnalyticsReport }) {
    const { context } = report;
    const included = new Map<string, [string, string]>([
        ["tasks", ["Задач", `${context.tasks_included} из ${context.tasks_total}`]],
        ["projects", ["Проектов", String(context.projects)]],
    ]);
    ([
        ["comments", context.comments_included], ["milestones", context.milestones_included], ["wbs_nodes", context.wbs_nodes_included],
        ["stickers", context.stickers_included], ["documents", context.documents_included], ["activity", context.activity_included],
    ] as const).forEach(([key, count]) => { if (count > 0) included.set(key, [CONTEXT_LABELS[key], String(count)]); });
    if (context.risks_total) included.set("risks", ["Рисков", `${context.risks_included ?? 0} из ${context.risks_total}`]);
    Object.entries(context.entity_counts ?? {}).forEach(([key, count]) => {
        if (count.total > 0) included.set(key, [CONTEXT_LABELS[key] ?? key, `${count.included} из ${count.total}`]);
    });
    const hasLimitations = context.truncated || context.omitted.length > 0;
    return <footer className="aris-report-footer">
        <div className="aris-report-provenance"><span><History size={13} aria-hidden="true" />Сохранённый разбор от <time dateTime={report.created_at}>{formatDateTime(report.created_at)}</time></span>
            <UserIdentity userId={report.created_by_user_id} name={report.created_by} /></div>
        <details className="aris-report-context">
            <summary><BookOpen size={15} aria-hidden="true" /><span>Контекст и источники разбора</span><span className="aris-context-coverage">{context.tasks_included} из {context.tasks_total} задач</span><ChevronDown size={15} aria-hidden="true" /></summary>
            <div className="aris-context-content">
                {(report.project_key || report.project_name) && <p>{report.project_key}{report.project_key && report.project_name && " · "}{report.project_name}</p>}
                <dl className="aris-context-counts">{Array.from(included, ([key, [label, count]]) => <div key={key}><dt>{label}</dt><dd>{count}</dd></div>)}</dl>
                <dl className="aris-context-technical"><div><dt>Модель разбора</dt><dd>{report.llm_model}</dd></div><div><dt>Время подготовки</dt><dd>{(report.duration_ms / 1000).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} с</dd></div></dl>
                {hasLimitations && <aside className="aris-context-limitations"><h4><Info size={15} aria-hidden="true" />Часть данных не вошла в разбор</h4>
                    {context.omitted.length > 0 ? <ul>{context.omitted.map(item => <li key={item}>{item}</li>)}</ul> : <p>Объём контекста был ограничен при подготовке разбора.</p>}</aside>}
            </div>
        </details>
        {hasLimitations && <p className="aris-context-limited"><Info size={13} aria-hidden="true" />Разбор учитывает часть данных. Ограничения указаны в контексте.</p>}
    </footer>;
}
