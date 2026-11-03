import type { ReactNode } from "react";
import { ArrowUpRight, CheckCircle2, Circle } from "lucide-react";
import { Link } from "react-router-dom";
import { cn } from "@/lib/cn";
import { Card } from "@/components/ui/Card";

export function OverviewSection({ title, description, action, children, className }: {
    title: string;
    description?: string;
    action?: ReactNode;
    children: ReactNode;
    className?: string;
}) {
    return <section className={cn("flex min-w-0 flex-col gap-3", className)}>
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
            <div className="min-w-0">
                <h2 className="text-[16px] font-semibold tracking-[-0.02em] text-primary">{title}</h2>
                {description && <p className="mt-1 text-[12px] leading-relaxed text-muted">{description}</p>}
            </div>
            {action}
        </div>
        {children}
    </section>;
}

export function OverviewPanel({ title, description, action, children, className }: {
    title: string;
    description?: string;
    action?: ReactNode;
    children: ReactNode;
    className?: string;
}) {
    return <Card className={cn("min-w-0 p-4 sm:p-5", className)}>
        <OverviewSection title={title} description={description} action={action}>
            {children}
        </OverviewSection>
    </Card>;
}

export function MetricGrid({ children }: { children: ReactNode }) {
    return <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">{children}</div>;
}

export function MetricCard({ label, value, hint, icon, tone = "default" }: {
    label: string;
    value: ReactNode;
    hint: string;
    icon: ReactNode;
    tone?: "default" | "success" | "danger" | "warning";
}) {
    const tones = { default: "text-primary", success: "text-success", danger: "text-danger", warning: "text-warning" };
    return <Card className="flex min-w-0 flex-col gap-3 p-4 sm:p-5">
        <div className="flex items-start justify-between gap-2 text-[14px] leading-snug text-secondary">
            <span>{label}</span>
            <span aria-hidden="true" className={cn("mt-0.5 shrink-0", tone === "default" ? "text-muted" : tones[tone])}>{icon}</span>
        </div>
        <div className={cn("text-[32px] leading-none font-semibold tracking-[-0.04em] tabular-nums", tones[tone])}>{value}</div>
        <p className="text-[12px] leading-relaxed text-muted">{hint}</p>
    </Card>;
}

/** Доля завершённых задач. Просрочка остаётся свойством открытых задач. */
export function TaskCompletion({ total, done, inProgress, color = "var(--color-success)" }: {
    total: number;
    done: number;
    inProgress: number;
    color?: string;
}) {
    const completed = Math.min(Math.max(done, 0), total);
    const remaining = Math.max(total - completed, 0);
    const percent = total > 0 ? Math.round(completed / total * 100) : 0;
    const circumference = 2 * Math.PI * 58;

    return <div className="flex flex-wrap items-center gap-x-6 gap-y-4 py-1">
        <div role="img" aria-label={total > 0 ? `Выполнено ${completed} из ${total} задач: ${percent}%` : "Задач пока нет"} className="relative size-40 shrink-0">
            <svg viewBox="0 0 144 144" className="size-full -rotate-90" aria-hidden="true">
                <circle cx="72" cy="72" r="58" fill="none" stroke="var(--color-border-strong)" strokeWidth="12" />
                {completed > 0 && <circle cx="72" cy="72" r="58" fill="none" stroke={color} strokeWidth="12"
                    strokeDasharray={`${(completed / total) * circumference} ${circumference}`} />}
            </svg>
            <div aria-hidden="true" className="absolute inset-0 flex flex-col items-center justify-center gap-1">
                <span className="text-[32px] leading-none font-semibold tracking-[-0.04em] text-primary tabular-nums">{total > 0 ? `${percent}%` : "—"}</span>
                <span className="text-[12px] text-muted">выполнено</span>
            </div>
        </div>
        <dl className="flex min-w-36 flex-1 flex-col gap-4">
            <div className="flex items-center justify-between gap-4">
                <dt className="flex items-center gap-2 text-[14px] text-secondary"><CheckCircle2 size={16} className="text-success" aria-hidden="true" />Завершено</dt>
                <dd className="text-[20px] font-semibold text-primary tabular-nums">{completed}</dd>
            </div>
            <div className="flex items-center justify-between gap-4">
                <dt className="flex items-center gap-2 text-[14px] text-secondary"><Circle size={16} className="text-muted" aria-hidden="true" />Открыто</dt>
                <dd className="text-[20px] font-semibold text-primary tabular-nums">{remaining}</dd>
            </div>
            <div className="border-t border-line-subtle pt-3 text-[12px] text-muted">
                Из открытых <span className="font-semibold text-secondary tabular-nums">{inProgress}</span> в работе
            </div>
        </dl>
    </div>;
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
