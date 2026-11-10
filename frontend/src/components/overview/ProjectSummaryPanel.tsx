import type { ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, ChevronDown, CircleCheckBig, Flame, History, Info, Lightbulb, RefreshCw, ShieldAlert, Sparkles } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDateTime, formatFullDate } from "@/lib/dates";
import { reportFreshness } from "@/lib/overview";
import type { AnalyticsHealth, AnalyticsReport } from "@/lib/types";
import { ANALYTICS_HEALTH_LABELS } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { ErrorMessage, Skeleton } from "@/components/ui/States";
import { AiOperationStatus } from "@/components/ui/AiOperationStatus";
import { FindingCard, ProgressCard, RecommendationCard } from "@/components/dashboard/AnalyticsCards";

const HEALTH_STYLES: Record<AnalyticsHealth, string> = {
    STABLE: "border-success/30 bg-success/10 text-success",
    WATCH: "border-accent-border bg-accent-soft text-accent",
    RISK: "border-warning/30 bg-warning/10 text-warning",
    CRITICAL: "border-danger/35 bg-danger/12 text-danger",
};

const HEALTH_ICONS: Record<AnalyticsHealth, typeof Activity> = {
    STABLE: CircleCheckBig,
    WATCH: Activity,
    RISK: ShieldAlert,
    CRITICAL: Flame,
};

const SIGNAL_ROWS: { key: "high_risks" | "occurred_risks" | "risks_review_overdue" | "risks_without_owner" | "risks_without_mitigation" | "overdue_tasks" | "milestones_at_risk" | "due_soon_tasks" | "blocked_tasks" | "stale_tasks" | "no_due_date_tasks" | "unassigned_tasks" | "unplaced_tasks"; label: string }[] = [
    { key: "high_risks", label: "высоких рисков" },
    { key: "occurred_risks", label: "реализовавшихся рисков" },
    { key: "risks_review_overdue", label: "рисков с просроченным контролем" },
    { key: "risks_without_owner", label: "рисков без ответственного" },
    { key: "risks_without_mitigation", label: "рисков без митигации" },
    { key: "overdue_tasks", label: "просроченных задач" },
    { key: "milestones_at_risk", label: "вех под угрозой" },
    { key: "due_soon_tasks", label: "задач со сроком на неделе" },
    { key: "blocked_tasks", label: "заблокированных задач" },
    { key: "stale_tasks", label: "задач без движения 2 недели" },
    { key: "no_due_date_tasks", label: "задач без срока" },
    { key: "unassigned_tasks", label: "задач без исполнителя" },
    { key: "unplaced_tasks", label: "задач вне разделов ИСР" },
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

    return <section aria-label="Вывод Ариса" className="ai-surface flex min-w-0 flex-col overflow-hidden rounded-[var(--radius-card)] border border-ai-border shadow-card">
        <header className="flex flex-wrap items-center gap-3 px-4 py-4 sm:px-5">
            <span className="ai-mark flex size-9 shrink-0 items-center justify-center rounded-[var(--radius-control)] text-ai-blue"><Sparkles size={18} aria-hidden="true" /></span>
            <div className="min-w-0 flex-1">
                <h2 className="text-[16px] font-semibold tracking-[-0.02em] text-primary">Вывод Ариса</h2>
                <p className="mt-0.5 text-[12px] leading-relaxed text-muted">{projectId ? "Оценка проекта на момент разбора" : "Оценка активных проектов и разовых задач"}</p>
            </div>
            <Button variant="ai" size="lg" onClick={() => generate.mutate()} disabled={generate.isPending || Boolean(blockedReason)} title={blockedReason}
                icon={generate.isPending ? <RefreshCw size={15} className="animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <Sparkles size={15} aria-hidden="true" />}>
                {generate.isPending ? "Разбираю…" : report ? "Обновить разбор" : "Разобрать"}
            </Button>
        </header>

        {blockedReason && <p className="px-4 pb-4 text-[12px] text-muted sm:px-5">{blockedReason}</p>}
        {reportQuery.error && <div className="px-4 pb-4 sm:px-5"><ErrorMessage title="Разбор недоступен" message={(reportQuery.error as Error).message}
            action={<Button size="sm" onClick={() => void reportQuery.refetch()}>Повторить загрузку</Button>} /></div>}
        {generate.error && <div className="px-4 pb-4 sm:px-5"><ErrorMessage title="Разбор не сформирован" message={(generate.error as Error).message} /></div>}
        {generate.isPending && <div className="border-t border-line-subtle px-4 py-3 sm:px-5">
            <AiOperationStatus message={projectId ? "Арис готовит разбор проекта…" : "Арис готовит разбор портфеля…"}
                description={`Сопоставляет задачи, сроки, риски и обсуждения. ${report ? "Ниже сохранён предыдущий разбор." : "Результат появится здесь."}`} />
        </div>}

        {reportQuery.isPending ? <div role="status" aria-label="Загрузка разбора" className="flex flex-col gap-3 border-t border-line-subtle p-5"><Skeleton className="h-5 w-2/3" /><Skeleton className="h-4 w-1/2" /></div> : report ? <>
            <div className="flex flex-col gap-3 border-t border-line-subtle px-4 py-4 sm:px-5">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-[12px]">
                    {HealthIcon && <span className={cn("inline-flex items-center gap-1.5 rounded-[var(--radius-control)] border px-2 py-1 font-medium", HEALTH_STYLES[report.health])}><HealthIcon size={14} aria-hidden="true" />Оценка разбора: {ANALYTICS_HEALTH_LABELS[report.health]}</span>}
                    <time dateTime={report.created_at} title={formatDateTime(report.created_at)} className="inline-flex items-center gap-1.5 text-muted"><History size={13} aria-hidden="true" />{formatFullDate(report.created_at)}</time>
                </div>
                {freshness === "stale" && <p className="text-[12px] leading-relaxed text-warning">Данные менялись после разбора. Обновите его, чтобы получить новую оценку.</p>}
                <p className="brand-summary-title max-w-4xl text-[18px] leading-snug font-semibold tracking-[-0.02em] text-primary">{report.headline}</p>
                <p className="line-clamp-3 max-w-4xl text-[14px] leading-relaxed text-secondary">{report.health_note}</p>
            </div>
            <details key={report.id} className="group border-t border-line-subtle">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3.5 text-[14px] font-medium text-accent hover:bg-hover sm:px-5 [&::-webkit-details-marker]:hidden">
                    <span>Полный разбор · {formatFullDate(report.created_at)}</span><ChevronDown size={17} className="shrink-0 group-open:rotate-180" aria-hidden="true" />
                </summary>
                <PulseDetail report={report} onOpenTask={onOpenTask} />
            </details>
        </> : !reportQuery.error && !generate.isPending && <p className="border-t border-line-subtle px-4 py-4 text-[14px] leading-relaxed text-muted sm:px-5">{projectId ? "Попросите Ариса оценить ход работ, риски и следующие шаги по проекту." : "Попросите Ариса сопоставить активные проекты и разовые задачи и подсказать, с чего начать."}</p>}
    </section>;
}

function PulseDetail({ report, onOpenTask }: { report: AnalyticsReport; onOpenTask: (taskId: number) => void }) {
    const signals = SIGNAL_ROWS.filter(row => (report.signals[row.key] ?? 0) > 0);
    return <div className="flex min-w-0 flex-col gap-5 px-4 pb-5 sm:px-5">
        <p className="max-w-4xl text-[14px] leading-relaxed text-secondary">{report.health_note}</p>
        {signals.length > 0 && <div className="flex flex-col gap-2">
            <h3 className="text-[12px] font-medium text-muted">Сигналы на момент разбора</h3>
            <ul className="flex flex-wrap gap-x-5 gap-y-2 text-[12px] text-secondary">{signals.map(row => <li key={row.key}><span className="font-semibold tabular-nums">{report.signals[row.key]}</span> {row.label}</li>)}</ul>
        </div>}
        <div className="grid items-start gap-5 lg:grid-cols-3">
            <PulseColumn title="Риски и препятствия" icon={<Flame size={15} aria-hidden="true" />} count={report.findings.length} empty="Проблем, требующих вмешательства, в разборе не найдено.">
                {report.findings.map((finding, index) => <FindingCard key={`${finding.title}-${index}`} finding={finding} onOpenTask={onOpenTask} />)}
            </PulseColumn>
            <PulseColumn title="Что сделано" icon={<CircleCheckBig size={15} aria-hidden="true" />} count={report.progress.length} empty="Заметных результатов в разборе не отмечено.">
                {report.progress.map((progress, index) => <ProgressCard key={`${progress.title}-${index}`} progress={progress} onOpenTask={onOpenTask} />)}
            </PulseColumn>
            <PulseColumn title="Рекомендации" icon={<Lightbulb size={15} aria-hidden="true" />} count={report.recommendations.length} empty="Дополнительных организационных действий в разборе нет.">
                {report.recommendations.map((recommendation, index) => <RecommendationCard key={`${recommendation.title}-${index}`} recommendation={recommendation} onOpenTask={onOpenTask} />)}
            </PulseColumn>
        </div>
        <PulseFooter report={report} />
    </div>;
}

function PulseColumn({ title, icon, count, empty, children }: { title: string; icon: ReactNode; count: number; empty: string; children: ReactNode }) {
    return <div className="flex min-w-0 flex-col gap-3">
        <h3 className="flex items-center gap-2 text-[14px] font-semibold text-secondary">{icon}{title}<span className="ml-auto text-[12px] font-normal text-muted tabular-nums">{count}</span></h3>
        {count === 0 ? <p className="text-[14px] leading-relaxed text-muted">{empty}</p> : <div className="flex flex-col gap-3">{children}</div>}
    </div>;
}

function PulseFooter({ report }: { report: AnalyticsReport }) {
    const { context } = report;
    const included = [
        `${context.tasks_included} из ${context.tasks_total} задач`,
        context.comments_included > 0 && `${context.comments_included} комментариев`,
        context.milestones_included > 0 && `${context.milestones_included} вех`,
        context.wbs_nodes_included > 0 && `${context.wbs_nodes_included} разделов ИСР`,
        context.stickers_included > 0 && `${context.stickers_included} стикеров`,
        context.documents_included > 0 && `${context.documents_included} документов`,
        (context.risks_total ?? 0) > 0 && `${context.risks_included ?? 0} из ${context.risks_total} рисков`,
        context.activity_included > 0 && `${context.activity_included} событий истории`,
        ...([
            ["members", "участников команды"], ["participants", "ролевых назначений"], ["attachments", "вложений"],
            ["dependencies", "зависимостей задач"], ["checklists", "чек-листов"], ["checklist_items", "пунктов чек-листов"], ["outside_tasks", "разовых задач"],
        ] as const).map(([key, label]) => {
            const count = context.entity_counts?.[key];
            return count && count.total > 0 && `${count.included} из ${count.total} ${label}`;
        }),
    ].filter(Boolean) as string[];

    return <footer className="flex flex-col gap-2 border-t border-line-subtle pt-4 text-[12px] leading-relaxed text-muted">
        <p>Сохранённый разбор от {formatFullDate(report.created_at)} · {report.created_by}</p>
        <p>В разбор вошли: {included.join(", ")}.</p>
        {context.truncated && context.omitted.length > 0 && <details>
            <summary className="inline-flex cursor-pointer items-center gap-1.5 hover:text-secondary"><Info size={13} aria-hidden="true" />Часть данных не вошла в разбор</summary>
            <ul className="mt-2 flex list-disc flex-col gap-1 pl-5">{context.omitted.map(item => <li key={item}>{item}</li>)}</ul>
        </details>}
    </footer>;
}
