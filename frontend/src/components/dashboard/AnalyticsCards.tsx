import type { ReactNode } from "react";
import {
    Ban,
    CalendarClock,
    CircleAlert,
    CircleCheckBig,
    Lightbulb,
    ShieldAlert,
    Workflow,
} from "lucide-react";
import { cn } from "@/lib/cn";
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

/**
 * Тон карточки несёт смысл вместе с текстом, а не вместо него: рядом с
 * цветной полосой всегда стоит подпись типа находки (раздел 16 гайда).
 */
type CardTone = "danger" | "warning" | "accent" | "success" | "neutral";

const TONE_STRIPE: Record<CardTone, string> = {
    danger: "bg-danger",
    warning: "bg-warning",
    accent: "bg-accent",
    success: "bg-success",
    neutral: "bg-line-strong",
};

const TONE_TEXT: Record<CardTone, string> = {
    danger: "text-danger",
    warning: "text-warning",
    accent: "text-accent",
    success: "text-success",
    neutral: "text-muted",
};

const FINDING_ICONS: Record<AnalyticsFindingKind, ReactNode> = {
    OVERDUE: <CalendarClock size={12} aria-hidden="true" />,
    RISK: <ShieldAlert size={12} aria-hidden="true" />,
    BLOCKER: <Ban size={12} aria-hidden="true" />,
    PROCESS: <Workflow size={12} aria-hidden="true" />,
    DATA_GAP: <CircleAlert size={12} aria-hidden="true" />,
};

const SEVERITY_TONES: Record<AnalyticsSeverity, CardTone> = {
    HIGH: "danger",
    MEDIUM: "warning",
    LOW: "neutral",
};

// Рекомендация — не проблема, поэтому красный тон ей не полагается даже на
// сегодняшнем горизонте: красный оставлен находкам (раздел 6 гайда).
const HORIZON_TONES: Record<AnalyticsHorizon, CardTone> = {
    TODAY: "warning",
    WEEK: "accent",
    LATER: "neutral",
};

interface TaskChipsProps {
    tasks: AnalyticsTaskRef[];
    onOpenTask: (taskId: number) => void;
}

/** Ссылки на задачи: свод должен открывать карточку, а не заставлять искать. */
function TaskChips({ tasks, onOpenTask }: TaskChipsProps) {
    if (tasks.length === 0) {
        return null;
    }
    return (
        <div className="flex flex-wrap gap-1 pt-0.5">
            {tasks.map((task) => (
                <button
                    key={task.id}
                    type="button"
                    title={`${task.key} · ${task.title}`}
                    onClick={() => onOpenTask(task.id)}
                    className={cn(
                        "inline-flex max-w-full items-center gap-1 rounded-[5px] border border-line",
                        "bg-surface px-2 py-1 font-mono text-[12px] text-muted",
                        "transition-colors duration-[var(--duration-fast)]",
                        "hover:border-accent-border hover:text-accent",
                    )}
                >
                    {task.is_overdue && (
                        <span aria-hidden="true" className="size-1 shrink-0 rounded-full bg-danger" />
                    )}
                    <span className="truncate">{task.key}</span>
                </button>
            ))}
        </div>
    );
}

interface InsightCardProps {
    tone: CardTone;
    label: string;
    icon: ReactNode;
    title: string;
    detail: string;
    projectKey: string | null;
    projectName: string | null;
    tasks: AnalyticsTaskRef[];
    onOpenTask: (taskId: number) => void;
}

/** Общая форма пункта свода: тип, заголовок, подтверждение и задачи. */
function InsightCard({
    tone,
    label,
    icon,
    title,
    detail,
    projectKey,
    projectName,
    tasks,
    onOpenTask,
}: InsightCardProps) {
    return (
        <article className="relative min-w-0 overflow-hidden rounded-[var(--radius-card)] border border-line-subtle bg-surface-2/55">
            <span
                aria-hidden="true"
                className={cn("absolute inset-y-0 left-0 w-[3px]", TONE_STRIPE[tone])}
            />
            <div className="flex min-w-0 flex-col gap-1.5 py-2.5 pr-3 pl-4">
                <div className="flex items-center gap-1.5">
                    <span
                        className={cn(
                            "inline-flex items-center gap-1 text-[12px] font-semibold",
                            TONE_TEXT[tone],
                        )}
                    >
                        {icon}
                        {label}
                    </span>
                    {projectKey && (
                        <span
                            title={projectName ?? projectKey}
                            className="ml-auto shrink-0 font-mono text-[12px] text-muted"
                        >
                            {projectKey}
                        </span>
                    )}
                </div>
                <h4 className="text-[14px] leading-snug font-semibold text-primary">{title}</h4>
                <p className="text-[14px] leading-relaxed text-muted">{detail}</p>
                <TaskChips tasks={tasks} onOpenTask={onOpenTask} />
            </div>
        </article>
    );
}

export function FindingCard({
    finding,
    onOpenTask,
}: {
    finding: AnalyticsFinding;
    onOpenTask: (taskId: number) => void;
}) {
    return (
        <InsightCard
            tone={SEVERITY_TONES[finding.severity]}
            label={ANALYTICS_FINDING_LABELS[finding.kind]}
            icon={FINDING_ICONS[finding.kind]}
            title={finding.title}
            detail={finding.detail}
            projectKey={finding.project_key}
            projectName={finding.project_name}
            tasks={finding.tasks}
            onOpenTask={onOpenTask}
        />
    );
}

export function ProgressCard({
    progress,
    onOpenTask,
}: {
    progress: AnalyticsProgress;
    onOpenTask: (taskId: number) => void;
}) {
    return (
        <InsightCard
            tone="success"
            label="Сделано"
            icon={<CircleCheckBig size={12} aria-hidden="true" />}
            title={progress.title}
            detail={progress.detail}
            projectKey={progress.project_key}
            projectName={progress.project_name}
            tasks={progress.tasks}
            onOpenTask={onOpenTask}
        />
    );
}

export function RecommendationCard({
    recommendation,
    onOpenTask,
}: {
    recommendation: AnalyticsRecommendation;
    onOpenTask: (taskId: number) => void;
}) {
    return (
        <InsightCard
            tone={HORIZON_TONES[recommendation.horizon]}
            label={ANALYTICS_HORIZON_LABELS[recommendation.horizon]}
            icon={<Lightbulb size={12} aria-hidden="true" />}
            title={recommendation.title}
            detail={recommendation.detail}
            projectKey={recommendation.project_key}
            projectName={recommendation.project_name}
            tasks={recommendation.tasks}
            onOpenTask={onOpenTask}
        />
    );
}
