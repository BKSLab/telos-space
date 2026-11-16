import { Link } from "react-router-dom";
import { AlertTriangle, ArrowUpRight, Circle, ShieldAlert } from "lucide-react";
import type { DashboardProject, DashboardTask } from "@/lib/types";
import { RISK_SIGNAL_LABELS } from "@/lib/risks";
import { OverviewPanel } from "@/components/dashboard/OverviewCards";
import { DueDate } from "@/components/ui/DueDate";
import { PriorityBadge } from "@/components/ui/Badge";

const TASK_PREVIEW = 3;
const RISK_PREVIEW = 2;

function ProjectRiskSignal({ project }: { project: DashboardProject }) {
    return <Link to={`/projects/${project.key}/risks`} className="flex min-w-0 items-start gap-2 rounded-[var(--radius-control)] py-2.5 hover:bg-hover focus-visible:outline-2 focus-visible:outline-accent">
        <ShieldAlert size={16} className="mt-0.5 shrink-0 text-warning" aria-hidden="true" />
        <div className="min-w-0 flex-1">
            <span title={project.name} className="block truncate text-[13px] font-medium text-primary">{project.name}</span>
            <span className="mt-0.5 block text-[12px] leading-relaxed text-muted [overflow-wrap:anywhere]">{project.risks?.signals.map(signal => `${RISK_SIGNAL_LABELS[signal.code]}: ${signal.count}`).join(" · ")}</span>
        </div>
        <ArrowUpRight size={15} className="mt-0.5 shrink-0 text-muted" aria-hidden="true" />
    </Link>;
}

function AttentionTask({ task, onOpen }: { task: DashboardTask; onOpen: (id: number) => void }) {
    const Icon = task.is_overdue ? AlertTriangle : Circle;
    return <button type="button" onClick={() => onOpen(task.id)}
        className="flex min-h-11 w-full min-w-0 items-start gap-2.5 rounded-[var(--radius-control)] py-2.5 text-left transition-colors hover:bg-hover focus-visible:outline-2 focus-visible:outline-accent">
        <Icon size={16} className={task.is_overdue ? "mt-0.5 shrink-0 text-danger" : "mt-0.5 shrink-0 text-muted"} aria-hidden="true" />
        <span className="min-w-0 flex-1">
            <span title={task.title} className="line-clamp-2 text-[13px] leading-snug font-medium text-primary [overflow-wrap:anywhere]">{task.title}</span>
            <span className="mt-1 block truncate text-[11px] text-muted"><span className="font-mono">{task.key}</span> · {task.stage_name} · {task.project_name}</span>
        </span>
        <span className="flex shrink-0 flex-col items-end gap-1.5">
            <DueDate value={task.due_date} className="text-[11px]" />
            <PriorityBadge priority={task.priority} className="text-[10px] tracking-normal normal-case" />
        </span>
    </button>;
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

    return <OverviewPanel title="Требует внимания" description="Открытые задачи и сигналы по рискам" icon={<AlertTriangle size={18} aria-hidden="true" />} className="h-full">
        <div className="flex flex-wrap gap-x-4 gap-y-1 border-b border-line-subtle pb-2.5 text-[12px]">
            <span className={overdue > 0 ? "text-danger" : "text-muted"}><span className="font-semibold tabular-nums">{overdue}</span> просроченных задач</span>
            <span className="text-muted"><span className="font-semibold tabular-nums">{risks.length}</span> проектов с сигналами риска</span>
        </div>
        {attention.length === 0 && risks.length === 0 ? <p className="py-3 text-[13px] leading-relaxed text-secondary">{overdue > 0
            ? "В портфеле есть просроченные задачи. Проверьте сроки в проектах."
            : "Срочных задач и сигналов по рискам нет. Можно продолжать плановую работу."}</p> : <>
            <div className="divide-y divide-line-subtle">{attention.slice(0, TASK_PREVIEW).map(task => <AttentionTask key={task.id} task={task} onOpen={onOpenTask} />)}</div>
            {attention.length > TASK_PREVIEW && <details className="text-[12px] text-secondary">
                <summary className="cursor-pointer py-2 text-accent hover:text-accent-hover">Ещё задач в подборке: {attention.length - TASK_PREVIEW}</summary>
                <div className="divide-y divide-line-subtle">{attention.slice(TASK_PREVIEW).map(task => <AttentionTask key={task.id} task={task} onOpen={onOpenTask} />)}</div>
            </details>}
            {risks.length > 0 && <div className="divide-y divide-line-subtle border-t border-line-subtle">{risks.slice(0, RISK_PREVIEW).map(project => <ProjectRiskSignal key={project.id} project={project} />)}</div>}
            {risks.length > RISK_PREVIEW && <details className="text-[12px] text-secondary">
                <summary className="cursor-pointer py-2 text-accent hover:text-accent-hover">Ещё проектов с сигналами: {risks.length - RISK_PREVIEW}</summary>
                <div className="divide-y divide-line-subtle">{risks.slice(RISK_PREVIEW).map(project => <ProjectRiskSignal key={project.id} project={project} />)}</div>
            </details>}
        </>}
    </OverviewPanel>;
}
