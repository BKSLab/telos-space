import type { ReactNode } from "react";
import { ArrowUpRight, Ban, CalendarClock, CircleAlert, CircleCheckBig, Lightbulb, ShieldAlert, Workflow } from "lucide-react";
import type {
    AnalyticsFinding,
    AnalyticsFindingKind,
    AnalyticsHorizon,
    AnalyticsProgress,
    AnalyticsRecommendation,
    AnalyticsSeverity,
    AnalyticsTaskRef,
} from "@/lib/types";
import { ANALYTICS_FINDING_LABELS, ANALYTICS_HORIZON_LABELS } from "@/lib/types";
import { formatFullDate } from "@/lib/dates";
import "@/components/overview/ProjectSummaryPanel.css";

type InsightTone = "danger" | "warning" | "accent" | "success" | "neutral";

const FINDING_ICONS: Record<AnalyticsFindingKind, ReactNode> = {
    OVERDUE: <CalendarClock size={17} aria-hidden="true" />,
    RISK: <ShieldAlert size={17} aria-hidden="true" />,
    BLOCKER: <Ban size={17} aria-hidden="true" />,
    PROCESS: <Workflow size={17} aria-hidden="true" />,
    DATA_GAP: <CircleAlert size={17} aria-hidden="true" />,
};

const SEVERITY_TONES: Record<AnalyticsSeverity, InsightTone> = { HIGH: "danger", MEDIUM: "warning", LOW: "neutral" };
const SEVERITY_LABELS: Record<AnalyticsSeverity, string> = { HIGH: "Высокая важность", MEDIUM: "Средняя важность", LOW: "Низкая важность" };
// Срок рекомендации не означает выполненную работу или критичность проблемы.
const HORIZON_TONES: Record<AnalyticsHorizon, InsightTone> = { TODAY: "warning", WEEK: "accent", LATER: "neutral" };

function TaskReferences({ tasks, onOpenTask }: { tasks: AnalyticsTaskRef[]; onOpenTask: (taskId: number) => void }) {
    if (tasks.length === 0) return null;
    return <ul className="aris-task-references" aria-label="Задачи, на которые опирается разбор">
        {tasks.map(task => {
            const context = [task.title, task.due_date && `Срок в разборе: ${formatFullDate(task.due_date)}`,
                task.is_overdue && "Просрочена на момент разбора"].filter(Boolean).join(". ");
            return <li key={task.id}>
                <button type="button" aria-label={task.key} aria-description={context} title={`${task.key} · ${context}`}
                    className="aris-task-reference touch-target" onClick={() => onOpenTask(task.id)}>
                    <span className="aris-task-key">{task.key}</span>
                    <span className="aris-task-title">{task.title}</span>
                    <ArrowUpRight size={13} aria-hidden="true" />
                </button>
            </li>;
        })}
    </ul>;
}

interface InsightRowProps {
    tone: InsightTone;
    kind: "finding" | "progress" | "recommendation";
    label: string;
    tag?: string;
    icon: ReactNode;
    title: string;
    detail: string;
    projectKey: string | null;
    projectName: string | null;
    currentProjectKey?: string | null;
    tasks: AnalyticsTaskRef[];
    onOpenTask: (taskId: number) => void;
}

/** Сохранённое наблюдение: смысловой маркер, полный текст и проверяемые задачи. */
function InsightRow({ tone, kind, label, tag, icon, title, detail, projectKey, projectName, currentProjectKey, tasks, onOpenTask }: InsightRowProps) {
    return <article className="aris-insight-row" data-tone={tone} data-kind={kind}>
        <span className="aris-insight-icon" aria-hidden="true">{icon}</span>
        <div className="aris-insight-content">
            <div className="aris-insight-meta">
                <span className="aris-insight-kind">{label}</span>
                {tag && <span className="aris-insight-tag">{tag}</span>}
                {(projectKey || projectName) && (!projectKey || projectKey !== currentProjectKey) && <span className="aris-insight-project" title={projectName ?? projectKey ?? undefined}>
                    {projectKey && <span className="font-mono">{projectKey}</span>}
                    {projectKey && projectName && <span aria-hidden="true"> · </span>}{projectName}
                </span>}
            </div>
            <h4>{title}</h4>
            <p>{detail}</p>
            <TaskReferences tasks={tasks} onOpenTask={onOpenTask} />
        </div>
    </article>;
}

export function FindingCard({ finding, currentProjectKey, onOpenTask }: { finding: AnalyticsFinding; currentProjectKey?: string | null; onOpenTask: (taskId: number) => void }) {
    return <InsightRow tone={SEVERITY_TONES[finding.severity]} kind="finding"
        label={ANALYTICS_FINDING_LABELS[finding.kind]} tag={SEVERITY_LABELS[finding.severity]}
        icon={FINDING_ICONS[finding.kind]} title={finding.title} detail={finding.detail}
        projectKey={finding.project_key} projectName={finding.project_name} currentProjectKey={currentProjectKey} tasks={finding.tasks} onOpenTask={onOpenTask} />;
}

export function ProgressCard({ progress, currentProjectKey, onOpenTask }: { progress: AnalyticsProgress; currentProjectKey?: string | null; onOpenTask: (taskId: number) => void }) {
    return <InsightRow tone="success" kind="progress" label="Сделано"
        icon={<CircleCheckBig size={17} aria-hidden="true" />} title={progress.title} detail={progress.detail}
        projectKey={progress.project_key} projectName={progress.project_name} currentProjectKey={currentProjectKey} tasks={progress.tasks} onOpenTask={onOpenTask} />;
}

export function RecommendationCard({ recommendation, currentProjectKey, onOpenTask }: { recommendation: AnalyticsRecommendation; currentProjectKey?: string | null; onOpenTask: (taskId: number) => void }) {
    return <InsightRow tone={HORIZON_TONES[recommendation.horizon]} kind="recommendation"
        label="Рекомендация" tag={ANALYTICS_HORIZON_LABELS[recommendation.horizon]}
        icon={<Lightbulb size={17} aria-hidden="true" />} title={recommendation.title} detail={recommendation.detail}
        projectKey={recommendation.project_key} projectName={recommendation.project_name} currentProjectKey={currentProjectKey} tasks={recommendation.tasks} onOpenTask={onOpenTask} />;
}
