import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowUpRight, CalendarClock, CheckCircle2, ChevronDown, ListTodo, Plus } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { ProjectStats, Task } from "@/lib/types";
import { formatDayMonth } from "@/lib/dates";
import { projectTaskFocus } from "@/lib/dashboard";
import { latestUpdate } from "@/lib/overview";
import { RISK_SIGNAL_LABELS } from "@/lib/risks";
import { useProjectOutlet } from "@/lib/useProjectOutlet";
import { useRenderedMarkdown } from "@/lib/useRenderedMarkdown";
import { useRiskSummary } from "@/lib/useRisks";
import { useUiStore } from "@/stores/ui";
import { Button, LinkButton } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DueDate } from "@/components/ui/DueDate";
import { EmptyState, ErrorMessage, Skeleton } from "@/components/ui/States";
import { ActionLink, MetricCard, MetricGrid, OverviewPanel, TaskCompletion } from "@/components/dashboard/OverviewCards";
import { OverviewTaskRow } from "@/components/dashboard/TaskRow";
import { ProjectSummaryPanel } from "@/components/overview/ProjectSummaryPanel";
import { StageBreakdown } from "@/components/overview/OverviewBreakdown";
import { ProjectPeopleSummary } from "@/components/projects/ProjectPeopleSummary";

const DEADLINES_PREVIEW = 5;

function Description({ markdown }: { markdown: string }) {
    const html = useRenderedMarkdown(markdown);
    return <div className="markdown-body text-[14px]" dangerouslySetInnerHTML={{ __html: html }} />;
}

export function ProjectOverviewPage() {
    const project = useProjectOutlet();
    const risks = useRiskSummary(project.id);
    const setSelectedTaskId = useUiStore(state => state.setSelectedTaskId);
    const statsQuery = useQuery({ queryKey: queryKeys.projectStats(project.id), queryFn: () => api.get<ProjectStats>(endpoints.projectStats(project.id)) });
    const tasksQuery = useQuery({ queryKey: queryKeys.tasks(project.id), queryFn: () => api.get<Task[]>(endpoints.projectTasks(project.id)) });
    const stats = statsQuery.data;
    const stageNames = new Map((stats?.stage_breakdown ?? []).map(item => [item.stage_id, item.stage_name]));
    const focus = projectTaskFocus(tasksQuery.data ?? [], stats?.stage_breakdown ?? []);
    const ready = Boolean(stats && tasksQuery.data);
    const taskList = `/projects/${project.key}/tasks`;
    const riskSummary = risks.data;

    return <div className="scrollbar-thin h-full overflow-y-auto">
        <div className="mx-auto flex w-full max-w-none flex-col gap-6 px-5 py-5 sm:px-6">
            <header className="flex flex-wrap items-center justify-between gap-3">
                <div>
                    <h1 className="text-[24px] font-semibold tracking-[-0.03em] text-primary">Обзор проекта</h1>
                    <p className="mt-1 text-[14px] text-muted">Текущее состояние работ и следующие шаги</p>
                </div>
                <LinkButton to={taskList} size="lg" icon={<ArrowUpRight size={16} aria-hidden="true" />}>Открыть задачи</LinkButton>
            </header>

            {statsQuery.error && <ErrorMessage title="Показатели недоступны" message={(statsQuery.error as Error).message}
                action={<Button size="sm" onClick={() => void statsQuery.refetch()}>Повторить загрузку</Button>} />}
            {statsQuery.isPending && <div role="status" aria-label="Загрузка показателей проекта"><MetricGrid>{[0, 1, 2, 3].map(index => <Skeleton key={index} className="h-36" />)}</MetricGrid></div>}
            {stats && <>
                <section aria-label="Показатели проекта">
                    <MetricGrid>
                        <MetricCard label="Всего задач" value={stats.total_tasks} hint={`${stats.in_progress_tasks} в работе`} icon={<ListTodo size={17} />} />
                        <MetricCard label="Выполнено" value={stats.total_tasks ? `${Math.round(stats.completion_rate * 100)}%` : "—"} hint={`${stats.done_tasks} задач завершено`} icon={<CheckCircle2 size={17} />} tone="success" />
                        <MetricCard label="Просрочено" value={stats.overdue_tasks} hint={stats.overdue_tasks ? "Пересмотрите сроки открытых задач" : "Открытых задач с просрочкой нет"} icon={<AlertTriangle size={17} />} tone={stats.overdue_tasks ? "danger" : "default"} />
                        <MetricCard label="Срок на неделе" value={stats.due_soon_tasks} hint="Сегодня и ближайшие 7 дней" icon={<CalendarClock size={17} />} tone={stats.due_soon_tasks ? "warning" : "default"} />
                    </MetricGrid>
                </section>
                {stats.total_tasks === 0 && <EmptyState title="В проекте пока нет задач" description="Добавьте первые задачи, чтобы видеть прогресс и планировать сроки."
                    icon={<ListTodo size={24} />} action={<LinkButton to={taskList} variant="primary" size="lg" icon={<Plus size={16} aria-hidden="true" />}>Перейти к задачам</LinkButton>} />}
                <div className="grid items-start gap-4 lg:grid-cols-2">
                    <OverviewPanel title="Фактический прогресс" description="Доля завершённых задач проекта">
                        <TaskCompletion total={stats.total_tasks} done={stats.done_tasks} inProgress={stats.in_progress_tasks} color={project.color} />
                        <p className="border-t border-line-subtle pt-3 text-[12px] leading-relaxed text-muted">Просрочка учитывается отдельно: у каждой задачи сохраняется её текущая стадия.</p>
                    </OverviewPanel>
                    <OverviewPanel title="По стадиям" description="Распределение всех задач по текущему состоянию">
                        {stats.stage_breakdown.length > 0 ? <StageBreakdown stages={stats.stage_breakdown} /> : <p className="py-5 text-[14px] text-muted">Стадии пока не настроены. Откройте доску проекта, чтобы организовать работу.</p>}
                    </OverviewPanel>
                </div>
            </>}

            <div className="grid items-start gap-4 lg:grid-cols-2">
                <OverviewPanel title="Требует внимания" description="Сроки открытых задач: просроченные идут первыми"
                    action={<Link to={`/projects/${project.key}/calendar`} className="text-[14px] text-accent hover:text-accent-hover">Календарь</Link>}>
                    {(tasksQuery.isPending || statsQuery.isPending) && <div role="status" aria-label="Загрузка сроков"><Skeleton className="h-40" /></div>}
                    {tasksQuery.error && <ErrorMessage title="Задачи недоступны" message={(tasksQuery.error as Error).message}
                        action={<Button size="sm" onClick={() => void tasksQuery.refetch()}>Повторить загрузку</Button>} />}
                    {!stats && statsQuery.isError && <p className="text-[14px] text-muted">После загрузки стадий здесь появятся сроки открытых задач.</p>}
                    {ready && (focus.scheduled.length === 0 ? <p className="py-5 text-[14px] leading-relaxed text-secondary">Открытых задач со сроками нет. Укажите даты в задачах, чтобы видеть ближайшие действия.</p> : <>
                        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12px]">
                            <span className={focus.overdue.length ? "text-danger" : "text-muted"}>{focus.overdue.length} просрочено</span>
                            <span className={focus.dueSoon.length ? "text-warning" : "text-muted"}>{focus.dueSoon.length} на ближайшие 7 дней</span>
                        </div>
                        <div className="divide-y divide-line-subtle">{focus.scheduled.slice(0, DEADLINES_PREVIEW).map(task => <OverviewTaskRow key={task.id} taskKey={task.key} title={task.title}
                            stage={stageNames.get(task.stage_id ?? 0)} priority={task.priority} meta={<DueDate value={task.due_date} className="text-[12px]" />} onOpen={() => setSelectedTaskId(task.id)} />)}</div>
                        {focus.scheduled.length > DEADLINES_PREVIEW && <Link to={taskList} className="text-[14px] text-accent hover:text-accent-hover">Все задачи со сроком: {focus.scheduled.length}</Link>}
                    </>)}
                </OverviewPanel>

                <OverviewPanel title="Следующие действия" description="Проверьте то, что мешает планировать работу">
                    <div className="divide-y divide-line-subtle">
                        {stats && <ActionLink to={`/projects/${project.key}/structure`} count={stats.unassigned_tasks} title="Размещение в ИСР"
                            description={stats.unassigned_tasks > 0 ? "Задачи вне разделов: определите место в структуре работ" : "Все задачи распределены по разделам ИСР"} />}
                        {ready && <ActionLink to={taskList} count={focus.withoutDeadline.length} title="Задачи без срока"
                            description={focus.withoutDeadline.length > 0 ? "Уточните даты открытых задач для планирования" : "У всех открытых задач указаны сроки"} />}
                        {riskSummary && <ActionLink to={`/projects/${project.key}/risks`} count={riskSummary.risks_due_for_review} title="Контроль рисков"
                            description={riskSummary.risks_due_for_review > 0 ? "Проверьте статус и меры реагирования" : "Нет рисков с наступившей датой контроля"} tone={riskSummary.risks_due_for_review ? "warning" : "default"} />}
                    </div>
                    {!stats && !ready && !riskSummary && <p className="py-3 text-[14px] text-muted">Действия появятся после загрузки данных проекта.</p>}
                </OverviewPanel>
            </div>

            <ProjectSummaryPanel projectId={project.id} onOpenTask={setSelectedTaskId}
                dataUpdatedAt={latestUpdate([...(tasksQuery.data ?? []), project, ...(riskSummary?.latest_update ? [{ updated_at: riskSummary.latest_update }] : [])])} />

            <OverviewPanel title="Риски проекта" description="Текущие данные реестра рисков"
                action={<Link to={`/projects/${project.key}/risks`} className="inline-flex items-center gap-1 text-[14px] text-accent hover:text-accent-hover">Открыть реестр<ArrowUpRight size={15} aria-hidden="true" /></Link>}>
                {risks.isPending && <div role="status" aria-label="Загрузка рисков"><Skeleton className="h-24" /></div>}
                {risks.error && <ErrorMessage title="Риски недоступны" message={risks.error.message} action={<Button size="sm" onClick={() => void risks.refetch()}>Повторить</Button>} />}
                {riskSummary && <>
                    <dl className="grid grid-cols-3 gap-3 py-2">
                        <div><dt className="text-[12px] text-muted">Активных</dt><dd className="mt-2 text-[28px] leading-none font-semibold text-primary tabular-nums">{riskSummary.active_risks}</dd></div>
                        <div><dt className="text-[12px] text-muted">Высоких</dt><dd className={`mt-2 text-[28px] leading-none font-semibold tabular-nums ${riskSummary.high_risks ? "text-danger" : "text-primary"}`}>{riskSummary.high_risks}</dd></div>
                        <div><dt className="text-[12px] text-muted">Реализовались</dt><dd className={`mt-2 text-[28px] leading-none font-semibold tabular-nums ${riskSummary.occurred_risks ? "text-danger" : "text-primary"}`}>{riskSummary.occurred_risks}</dd></div>
                    </dl>
                    {riskSummary.signals.length > 0 ? <ul className="flex flex-wrap gap-x-5 gap-y-2 border-t border-line-subtle pt-3 text-[14px] text-secondary">
                        {riskSummary.signals.map(signal => <li key={signal.code}>{RISK_SIGNAL_LABELS[signal.code]}: <span className="font-semibold tabular-nums">{signal.count}</span></li>)}
                    </ul> : <p className="text-[14px] text-muted">Сигналов, требующих вмешательства, нет.</p>}
                </>}
            </OverviewPanel>

            <Card className="min-w-0 p-4 sm:p-5">
                <details className="group">
                    <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-[16px] font-semibold text-primary [&::-webkit-details-marker]:hidden">О проекте<ChevronDown size={18} className="text-muted group-open:rotate-180" aria-hidden="true" /></summary>
                    <div className="mt-4 flex flex-col gap-4 [&_dl]:text-[14px]">
                        <ProjectPeopleSummary project={project} />
                        {project.description_md ? <Description markdown={project.description_md} /> : <p className="text-[14px] text-muted">Описание проекта пока не заполнено.</p>}
                        <dl className="flex flex-wrap gap-x-6 gap-y-3 border-t border-line-subtle pt-4 text-[12px]">
                            {project.start_date && <div><dt className="text-muted">Старт</dt><dd className="mt-1 text-secondary">{formatDayMonth(project.start_date)}</dd></div>}
                            {project.due_date && <div><dt className="text-muted">Плановое завершение</dt><dd className="mt-1 text-secondary">{formatDayMonth(project.due_date)}</dd></div>}
                            {stats?.next_due_date && <div><dt className="text-muted">Ближайший срок задачи</dt><dd className="mt-1 text-secondary">{formatDayMonth(stats.next_due_date)}</dd></div>}
                        </dl>
                    </div>
                </details>
            </Card>
        </div>
    </div>;
}
