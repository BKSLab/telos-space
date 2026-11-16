import type { ReactNode } from "react";
import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";
import { cn } from "@/lib/cn";
import { Card } from "@/components/ui/Card";
import type { StageBreakdown } from "@/lib/types";
import { DistributionRing } from "./DashboardCharts";

export function OverviewSection({ title, description, action, children, className, icon }: {
    title: string;
    description?: string;
    action?: ReactNode;
    children: ReactNode;
    className?: string;
    icon?: ReactNode;
}) {
    return <section aria-label={title} className={cn("flex min-w-0 flex-col gap-3", className)}>
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
            <div className="min-w-0">
                <h2 className="dashboard-panel-title text-[16px] font-semibold tracking-[-0.02em] text-primary">{icon}{title}</h2>
                {description && <p className="mt-1 text-[12px] leading-relaxed text-muted">{description}</p>}
            </div>
            {action}
        </div>
        {children}
    </section>;
}

export function OverviewPanel({ title, description, action, children, className, icon }: {
    title: string;
    description?: string;
    action?: ReactNode;
    children: ReactNode;
    className?: string;
    icon?: ReactNode;
}) {
    return <Card className={cn("min-w-0 p-4 sm:p-5", className)}>
        <OverviewSection title={title} description={description} action={action} icon={icon}>
            {children}
        </OverviewSection>
    </Card>;
}

export function MetricGrid({ children, className }: { children: ReactNode; className?: string }) {
    return <div className={cn("grid grid-cols-2 gap-3 sm:grid-cols-4", className)}>{children}</div>;
}

export function MetricCard({ label, value, hint, icon, tone = "default" }: {
    label: string;
    value: ReactNode;
    hint: string;
    icon: ReactNode;
    tone?: "default" | "success" | "danger" | "warning";
}) {
    const tones = { default: "text-primary", success: "text-success", danger: "text-danger", warning: "text-warning" };
    return <Card data-tone={tone} className="dashboard-metric flex min-w-0 flex-col gap-2.5 p-3.5 sm:p-4">
        <div className="flex items-start justify-between gap-2 text-[14px] leading-snug text-secondary">
            <span>{label}</span>
            <span aria-hidden="true" className="dashboard-metric-icon shrink-0">{icon}</span>
        </div>
        <div className={cn("text-[32px] leading-none font-semibold tracking-[-0.04em] tabular-nums", tones[tone])}>{value}</div>
        <p className="text-[12px] leading-snug text-muted">{hint}</p>
    </Card>;
}

/** Доля завершённых задач; просрочка остаётся отдельным свойством открытых задач. */
export function TaskCompletion({ total, done, inProgress, color = "var(--color-success)", stages }: {
    total: number;
    done: number;
    inProgress: number;
    color?: string;
    stages?: StageBreakdown[];
}) {
    const count = Number.isFinite(total) ? Math.max(0, total) : 0;
    const completed = Number.isFinite(done) ? Math.min(Math.max(done, 0), count) : 0;
    const remaining = count - completed;
    const active = Number.isFinite(inProgress) ? Math.min(Math.max(inProgress, 0), remaining) : 0;
    const percent = count > 0 ? Math.round(completed / count * 100) : 0;
    const validStages = stages && stages.length > 0 && stages.reduce((sum, stage) => sum + stage.tasks_count, 0) === count;
    const segments = validStages ? stages.map(stage => ({ id: stage.stage_id, label: stage.stage_name, value: stage.tasks_count, color: stage.color })) : [
        { id: "done", label: "Завершено", value: completed, color },
        { id: "active", label: "В работе", value: active, color: "var(--color-accent)" },
        { id: "other", label: "Остальные открытые", value: remaining - active, color: "var(--color-border-strong)" },
    ];
    return <DistributionRing label={count > 0 ? `Выполнено ${completed} из ${count} задач: ${percent}%` : "Задач пока нет"}
        segments={segments} centerValue={`${percent}%`} centerLabel="выполнено" emptyLabel="Задач пока нет" />;
}

export function ActionLink({ to, title, description, count, tone = "default" }: {
    to: string;
    title: string;
    description: string;
    count?: number;
    tone?: "default" | "danger" | "warning";
}) {
    return <Link to={to} className="flex min-w-0 items-center gap-3 rounded-[var(--radius-control)] px-2 py-3 transition-colors hover:bg-hover">
        {count !== undefined && <span className={cn("min-w-9 text-[24px] leading-none font-semibold tabular-nums", tone === "danger" ? "text-danger" : tone === "warning" ? "text-warning" : "text-primary")}>{count}</span>}
        <div className="min-w-0 flex-1">
            <span className="block text-[14px] font-medium text-primary">{title}</span>
            <span className="mt-0.5 block text-[12px] leading-relaxed text-muted">{description}</span>
        </div>
        <ArrowUpRight size={16} className="shrink-0 text-muted" aria-hidden="true" />
    </Link>;
}
