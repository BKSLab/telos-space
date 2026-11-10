import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, FolderKanban, ListTodo } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import { latestUpdate } from "@/lib/overview";
import { cn } from "@/lib/cn";
import { fullName, type MyTask, type Dashboard } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { DueDate } from "@/components/ui/DueDate";
import { ErrorMessage, Skeleton } from "@/components/ui/States";
import { ProjectSummaryPanel } from "@/components/overview/ProjectSummaryPanel";
import { PortfolioAttention } from "@/components/dashboard/PortfolioAttention";
import { OverviewTaskRow, TaskRow } from "@/components/dashboard/TaskRow";
import { MetricCard, MetricGrid, OverviewPanel, TaskCompletion } from "@/components/dashboard/OverviewCards";
import { useUiStore } from "@/stores/ui";

function DashboardSkeleton() {
    return <div role="status" aria-label="Загрузка сводки" className="flex flex-col gap-4">
        <MetricGrid>{[0, 1, 2, 3].map(index => <Skeleton key={index} className="h-36" />)}</MetricGrid>
        <div className="grid gap-4 lg:grid-cols-2"><Skeleton className="h-72" /><Skeleton className="h-72" /></div>
        <Skeleton className="h-44" />
    </div>;
}

export function PortfolioSummary() {
    const setSelectedTaskId = useUiStore(state => state.setSelectedTaskId);
    const dashboardQuery = useQuery({ queryKey: queryKeys.dashboard, queryFn: () => api.get<Dashboard>(endpoints.dashboard()) });
    const personalTasks = useQuery({ queryKey: queryKeys.myTaskList("all", false), queryFn: () => api.get<MyTask[]>(endpoints.myTasks("all", false)) });
    const personalRows = (personalTasks.data ?? []).filter(task => task.project_id === null);
    const showPersonal = personalTasks.isPending || !!personalTasks.error || personalRows.length > 0;
    const data = dashboardQuery.data;
    const activeProjects = (data?.projects ?? []).filter(project => project.status === "ACTIVE");
    return <section aria-label="Сводка портфеля" className="flex min-w-0 flex-col gap-5 sm:gap-6">
        {dashboardQuery.isPending && <DashboardSkeleton />}
        {dashboardQuery.error && <ErrorMessage message={(dashboardQuery.error as Error).message}
            action={<Button size="sm" onClick={() => void dashboardQuery.refetch()}>Повторить загрузку</Button>} />}

        {data && <>
            <section aria-label="Показатели портфеля" className="flex flex-col gap-2.5">
                <MetricGrid>
                    <MetricCard label="Проекты в работе" value={data.totals.active_projects} hint={`Всего доступно: ${data.totals.total_projects}`} icon={<FolderKanban size={17} />} />
                    <MetricCard label="Задачи в работе" value={data.totals.in_progress_tasks} hint={`Во всех проектах: ${data.totals.total_tasks}`} icon={<ListTodo size={17} />} />
                    <MetricCard label="Выполнено" value={data.totals.total_tasks ? `${Math.round(data.totals.completion_rate * 100)}%` : "—"} hint={`${data.totals.done_tasks} задач завершено`} icon={<CheckCircle2 size={17} />} tone="success" />
                    <MetricCard label="Просрочено" value={data.totals.overdue_tasks} hint={data.totals.overdue_tasks > 0 ? "Проверьте сроки открытых задач" : "Открытых задач с просрочкой нет"} icon={<AlertTriangle size={17} />} tone={data.totals.overdue_tasks > 0 ? "danger" : "default"} />
                </MetricGrid>
                <p className="text-[12px] text-muted">Показатели по всем доступным проектам. Разовые задачи показаны отдельно.</p>
            </section>

            <div className="grid items-start gap-4 lg:grid-cols-2">
                <OverviewPanel title="Фактический прогресс" description="Текущее состояние задач всех доступных проектов">
                    <TaskCompletion total={data.totals.total_tasks} done={data.totals.done_tasks} inProgress={data.totals.in_progress_tasks} />
                    <p className="border-t border-line-subtle pt-3 text-[12px] leading-relaxed text-muted">
                        {data.totals.total_tasks > 0 ? "Прогресс — доля завершённых задач. Просроченные входят в число открытых." : "Добавьте задачи в проект, чтобы увидеть прогресс работ."}
                    </p>
                </OverviewPanel>

                <PortfolioAttention projects={data.projects} tasks={data.attention_tasks} overdue={data.totals.overdue_tasks} onOpenTask={setSelectedTaskId} />
            </div>

            <ProjectSummaryPanel onOpenTask={setSelectedTaskId}
                dataUpdatedAt={latestUpdate([...data.projects, ...data.attention_tasks, ...data.recent_tasks, ...personalRows])}
                blockedReason={activeProjects.length > 0 || data.has_outside_tasks ? undefined : "Нет проектов в работе и открытых разовых задач"} />

        </>}

        <div className={cn("grid items-start gap-4", showPersonal && data && "xl:grid-cols-2")}>
            {showPersonal && <OverviewPanel title="Разовые задачи" description="Открытые задачи вне проектов · в KPI портфеля не входят"
                action={<Link to="/my-tasks" className="text-[14px] text-accent hover:text-accent-hover">Мои задачи{personalRows.length > 0 ? ` · ${personalRows.length}` : ""}</Link>}>
                {personalTasks.isPending && <Skeleton className="h-28" />}
                {personalTasks.error && <ErrorMessage message={(personalTasks.error as Error).message} action={<Button size="sm" onClick={() => void personalTasks.refetch()}>Повторить</Button>} />}
                <div className="divide-y divide-line-subtle">{personalRows.slice(0, 6).map(task => {
                    const executor = task.participants?.find(person => person.role === "EXECUTOR")?.user;
                    return <OverviewTaskRow key={task.id} taskKey={task.key} title={task.title} stage={`${task.state_label}${executor ? ` · ${fullName(executor)}` : ""}`}
                        priority={task.priority} meta={<DueDate value={task.due_date} className="text-[12px]" />} onOpen={() => setSelectedTaskId(task.id)} />;
                })}</div>
            </OverviewPanel>}
            {data && <OverviewPanel title="Недавно изменённые" description="Последние изменения в доступных проектах">
                {data.recent_tasks.length === 0 ? <p className="py-5 text-[14px] text-muted">Изменений пока не было.</p> : <div className="divide-y divide-line-subtle">{data.recent_tasks.map(task => <TaskRow key={task.id} task={task} showUpdated onOpen={setSelectedTaskId} />)}</div>}
            </OverviewPanel>}
        </div>
    </section>;
}
