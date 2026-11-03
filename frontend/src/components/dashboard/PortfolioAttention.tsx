import { Link } from "react-router-dom";
import { ArrowUpRight, ShieldAlert } from "lucide-react";
import type { DashboardProject, DashboardTask } from "@/lib/types";
import { RISK_SIGNAL_LABELS } from "@/lib/risks";
import { OverviewPanel } from "@/components/dashboard/OverviewCards";
import { TaskRow } from "@/components/dashboard/TaskRow";

const TASK_PREVIEW = 4;
const RISK_PREVIEW = 2;

function ProjectRiskSignal({ project }: { project: DashboardProject }) {
    return <Link to={`/projects/${project.key}/risks`} className="flex items-start gap-2 rounded-[var(--radius-control)] px-2 py-3 hover:bg-hover">
        <ShieldAlert size={16} className="mt-0.5 shrink-0 text-warning" aria-hidden="true" />
        <div className="min-w-0 flex-1">
            <span className="block text-[14px] font-medium text-primary">{project.name}</span>
            <span className="mt-1 block text-[12px] leading-relaxed text-muted">{project.risks?.signals.map(signal => `${RISK_SIGNAL_LABELS[signal.code]}: ${signal.count}`).join(" · ")}</span>
        </div>
        <ArrowUpRight size={15} className="mt-0.5 shrink-0 text-muted" aria-hidden="true" />
    </Link>;
}

export function PortfolioAttention({ projects, tasks, overdue, onOpenTask }: {
    projects: DashboardProject[];
    tasks: DashboardTask[];
    overdue: number;
    onOpenTask: (taskId: number) => void;
}) {
    const risks = projects.filter(project => project.status === "ACTIVE" && (project.risks?.signals.length ?? 0) > 0)
        .sort((first, second) => (second.risks?.high_risks ?? 0) - (first.risks?.high_risks ?? 0));
    const attention = [...tasks].sort((first, second) => (first.due_date ?? "").localeCompare(second.due_date ?? ""));

    return <OverviewPanel title="Требует внимания" description="Проверьте просрочки, ближайшие сроки и меры по рискам">
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12px]">
            <span className={overdue > 0 ? "text-danger" : "text-muted"}>{overdue} просроченных задач</span>
            <span className="text-muted">{risks.length} проектов в работе с сигналами риска</span>
        </div>
        {attention.length === 0 && risks.length === 0 ? <p className="py-5 text-[14px] leading-relaxed text-secondary">Срочных задач и сигналов по рискам нет. Можно продолжать плановую работу.</p> : <>
            <div className="divide-y divide-line-subtle">{attention.slice(0, TASK_PREVIEW).map(task => <TaskRow key={task.id} task={task} onOpen={onOpenTask} />)}</div>
            {attention.length > TASK_PREVIEW && <details className="text-[14px] text-secondary">
                <summary className="cursor-pointer py-2 text-accent hover:text-accent-hover">Ещё задач в подборке: {attention.length - TASK_PREVIEW}</summary>
                <div className="divide-y divide-line-subtle">{attention.slice(TASK_PREVIEW).map(task => <TaskRow key={task.id} task={task} onOpen={onOpenTask} />)}</div>
            </details>}
            {risks.length > 0 && <div className="divide-y divide-line-subtle border-t border-line-subtle">{risks.slice(0, RISK_PREVIEW).map(project => <ProjectRiskSignal key={project.id} project={project} />)}</div>}
            {risks.length > RISK_PREVIEW && <details className="text-[14px] text-secondary">
                <summary className="cursor-pointer py-2 text-accent hover:text-accent-hover">Ещё проектов с сигналами: {risks.length - RISK_PREVIEW}</summary>
                <div className="divide-y divide-line-subtle">{risks.slice(RISK_PREVIEW).map(project => <ProjectRiskSignal key={project.id} project={project} />)}</div>
            </details>}
        </>}
    </OverviewPanel>;
}
