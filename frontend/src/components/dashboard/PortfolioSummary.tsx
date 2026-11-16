import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, CalendarDays, ChartNoAxesCombined, ChartPie, History, ListTodo } from "lucide-react";
import { api, endpoints, queryKeys } from "@/lib/api";
import { latestUpdate } from "@/lib/overview";
import { cn } from "@/lib/cn";
import { PROJECT_STATUS_LABELS, type MyTask, type Dashboard, type DashboardProject, type ProjectStatus } from "@/lib/types";
import { dueTone } from "@/lib/dates";
import { Button } from "@/components/ui/Button";
import { DueDate } from "@/components/ui/DueDate";
import { ErrorMessage, Skeleton } from "@/components/ui/States";
import { ProjectSummaryPanel } from "@/components/overview/ProjectSummaryPanel";
import { PortfolioAttention } from "@/components/dashboard/PortfolioAttention";
import { PortfolioMetrics } from "@/components/dashboard/PortfolioMetrics";
import { DistributionRing } from "@/components/dashboard/DashboardCharts";
import { OverviewTaskRow, TaskRow } from "@/components/dashboard/TaskRow";
import { MetricGrid, OverviewPanel, TaskCompletion } from "@/components/dashboard/OverviewCards";
import { useUiStore } from "@/stores/ui";

function DashboardSkeleton() {
    return <div role="status" aria-label="Загрузка сводки" className="flex flex-col gap-4">
        <MetricGrid className="sm:grid-cols-5">{[0, 1, 2, 3, 4].map(index => <Skeleton key={index} className="h-28" />)}</MetricGrid>
        <div className="grid gap-4 lg:grid-cols-2"><Skeleton className="h-72" /><Skeleton className="h-72" /></div>
        <Skeleton className="h-44" />
    </div>;
}

const STATUS_COLORS: Record<ProjectStatus, string> = {
    ACTIVE: "var(--color-accent)",
    PLANNING: "var(--color-text-secondary)",
    PAUSED: "var(--color-warning)",
    COMPLETED: "var(--color-success)",
    ARCHIVED: "var(--color-border-strong)",
};
const STATUS_ORDER: ProjectStatus[] = ["ACTIVE", "PLANNING", "PAUSED", "COMPLETED", "ARCHIVED"];

function PortfolioDeadlines({ projects }: { projects: DashboardProject[] }) {
    const deadlines = projects.filter(project => project.status !== "ARCHIVED" && project.status !== "COMPLETED"
        && project.next_due_date !== null && dueTone(project.next_due_date) !== "danger")
        .sort((first, second) => first.next_due_date!.localeCompare(second.next_due_date!));
    const row = (project: DashboardProject) => <Link key={project.id} to={`/projects/${project.key}/tasks`}
        className="group flex min-h-11 min-w-0 items-center gap-3 rounded-[var(--radius-control)] py-3 transition-colors hover:bg-hover focus-visible:outline-2 focus-visible:outline-accent">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-surface-2 text-muted"><CalendarDays size={16} aria-hidden="true" /></span>
        <span className="min-w-0 flex-1">
            <span title={project.name} className="block truncate text-[13px] font-medium text-primary group-hover:text-accent">{project.name}</span>
            <span className="mt-0.5 block font-mono text-[11px] text-muted">{project.key}</span>
        </span>
        <DueDate value={project.next_due_date} className="shrink-0 text-[12px]" />
        <ArrowUpRight size={14} className="shrink-0 text-muted" aria-hidden="true" />
    </Link>;

    return <OverviewPanel title="Ближайшие сроки" description="Сроки ближайших задач по проектам" icon={<CalendarDays size={18} aria-hidden="true" />} className="h-full">
        {deadlines.length > 0 ? <>
            <div className="divide-y divide-line-subtle">{deadlines.slice(0, 3).map(row)}</div>
            {deadlines.length > 3 && <details className="text-[12px] text-secondary">
                <summary className="cursor-pointer py-2 text-accent hover:text-accent-hover">Ещё проектов со сроками: {deadlines.length - 3}</summary>
                <div className="divide-y divide-line-subtle">{deadlines.slice(3).map(row)}</div>
            </details>}
        </> : <p className="py-4 text-[13px] leading-relaxed text-muted">Предстоящих сроков задач пока нет.</p>}
    </OverviewPanel>;
}

export function PortfolioSummary() {
    const setSelectedTaskId = useUiStore(state => state.setSelectedTaskId);
    const dashboardQuery = useQuery({ queryKey: queryKeys.dashboard, queryFn: () => api.get<Dashboard>(endpoints.dashboard()) });
    const personalTasks = useQuery({ queryKey: queryKeys.myTaskList("all", false), queryFn: () => api.get<MyTask[]>(endpoints.myTasks("all", false)) });
    const personalRows = (personalTasks.data ?? []).filter(task => task.project_id === null);
    const personalRow = (task: MyTask) => {
        const executor = task.participants?.find(person => person.role === "EXECUTOR")?.user;
        return <OverviewTaskRow key={task.id} taskKey={task.key} title={task.title} stage={task.state_label} person={executor}
            priority={task.priority} meta={<DueDate value={task.due_date} className="text-[12px]" />} onOpen={() => setSelectedTaskId(task.id)} />;
    };
    const showPersonal = personalTasks.isPending || !!personalTasks.error || personalRows.length > 0;
    const data = dashboardQuery.data;
    const activeProjects = (data?.projects ?? []).filter(project => project.status === "ACTIVE");
    const statuses = STATUS_ORDER.map(status => ({ id: status, label: PROJECT_STATUS_LABELS[status],
        value: data?.projects.filter(project => project.status === status).length ?? 0, color: STATUS_COLORS[status] }));
    return <section aria-label="Сводка портфеля" className="flex min-w-0 flex-col gap-4">
        {dashboardQuery.isPending && <DashboardSkeleton />}
        {dashboardQuery.error && <ErrorMessage message={(dashboardQuery.error as Error).message}
            action={<Button size="sm" onClick={() => void dashboardQuery.refetch()}>Повторить загрузку</Button>} />}

        {data && <>
            <PortfolioMetrics data={data} />

            <div className="grid items-stretch gap-4 lg:grid-cols-2">
                <OverviewPanel title="Выполнение задач" description="Текущее состояние задач портфеля" icon={<ChartNoAxesCombined size={18} aria-hidden="true" />} className="h-full">
                    <TaskCompletion total={data.totals.total_tasks} done={data.totals.done_tasks} inProgress={data.totals.in_progress_tasks} />
                    {data.totals.total_tasks === 0 && <p className="text-[12px] text-muted">Добавьте задачи в проект, чтобы увидеть прогресс работ.</p>}
                </OverviewPanel>
                <OverviewPanel title="Проекты по статусам" description="Распределение всех доступных проектов" icon={<ChartPie size={18} aria-hidden="true" />} className="h-full">
                    <DistributionRing label={`Статусы проектов: всего ${data.projects.length}`} segments={statuses}
                        centerValue={data.projects.length} centerLabel="проектов" emptyLabel="Проектов пока нет" />
                </OverviewPanel>
            </div>
            <div className="grid items-stretch gap-4 lg:grid-cols-2">
                <PortfolioAttention projects={data.projects} tasks={data.attention_tasks} overdue={data.totals.overdue_tasks} onOpenTask={setSelectedTaskId} />
                <PortfolioDeadlines projects={data.projects} />
            </div>

            <ProjectSummaryPanel onOpenTask={setSelectedTaskId}
                dataUpdatedAt={latestUpdate([...data.projects, ...data.attention_tasks, ...data.recent_tasks, ...personalRows])}
                blockedReason={activeProjects.length > 0 || data.has_outside_tasks ? undefined : "Нет проектов в работе и открытых разовых задач"} />

        </>}

        <div className={cn("grid items-start gap-4", showPersonal && data && "xl:grid-cols-2")}>
            {showPersonal && <OverviewPanel title="Разовые задачи" description="Открытые задачи вне проектов · в KPI портфеля не входят" icon={<ListTodo size={18} aria-hidden="true" />}
                action={<Link to="/my-tasks" className="text-[14px] text-accent hover:text-accent-hover">Мои задачи{personalRows.length > 0 ? ` · ${personalRows.length}` : ""}</Link>}>
                {personalTasks.isPending && <Skeleton className="h-28" />}
                {personalTasks.error && <ErrorMessage message={(personalTasks.error as Error).message} action={<Button size="sm" onClick={() => void personalTasks.refetch()}>Повторить</Button>} />}
                <div className="divide-y divide-line-subtle">{personalRows.slice(0, 4).map(personalRow)}</div>
                {personalRows.length > 4 && <details className="text-[12px] text-secondary">
                    <summary className="cursor-pointer py-2 text-accent hover:text-accent-hover">Ещё разовых задач: {personalRows.length - 4}</summary>
                    <div className="divide-y divide-line-subtle">{personalRows.slice(4).map(personalRow)}</div>
                </details>}
            </OverviewPanel>}
            {data && <OverviewPanel title="Недавно изменённые" description="Последние изменения в доступных проектах" icon={<History size={18} aria-hidden="true" />}>
                {data.recent_tasks.length === 0 ? <p className="py-4 text-[13px] text-muted">Изменений пока не было.</p> : <>
                    <div className="divide-y divide-line-subtle">{data.recent_tasks.slice(0, 4).map(task => <TaskRow key={task.id} task={task} showUpdated onOpen={setSelectedTaskId} />)}</div>
                    {data.recent_tasks.length > 4 && <details className="text-[12px] text-secondary">
                        <summary className="cursor-pointer py-2 text-accent hover:text-accent-hover">Ещё изменений: {data.recent_tasks.length - 4}</summary>
                        <div className="divide-y divide-line-subtle">{data.recent_tasks.slice(4).map(task => <TaskRow key={task.id} task={task} showUpdated onOpen={setSelectedTaskId} />)}</div>
                    </details>}
                </>}
            </OverviewPanel>}
        </div>
    </section>;
}
