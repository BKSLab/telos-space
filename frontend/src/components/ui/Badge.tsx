import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import type { ProjectStatus, TaskPriority } from "@/lib/types";
import { PRIORITY_LABELS, PROJECT_STATUS_LABELS } from "@/lib/types";

interface BadgeProps {
    children: ReactNode;
    className?: string;
    title?: string;
}

export function Badge({ children, className, title }: BadgeProps) {
    return (
        <span
            title={title}
            className={cn(
                "inline-flex items-center gap-1 rounded-[5px] border border-line-subtle",
                "bg-white/[0.025] px-1.5 py-0.5 text-[11px] font-medium text-muted",
                className,
            )}
        >
            {children}
        </span>
    );
}

/**
 * Маркер состояния. Состояние всегда передаётся и цветом, и текстом —
 * цвет не может быть единственным носителем смысла (раздел 16).
 */
export function StatusDot({ color, className }: { color: string; className?: string }) {
    return (
        <span
            aria-hidden="true"
            style={{ backgroundColor: color }}
            className={cn("inline-block size-1.5 shrink-0 rounded-full", className)}
        />
    );
}

const PRIORITY_VARIANTS: Record<TaskPriority, string> = {
    LOW: "border-priority-low/30 bg-priority-low/10 text-priority-low",
    MEDIUM: "border-priority-medium/30 bg-priority-medium/10 text-priority-medium",
    HIGH: "border-priority-high/30 bg-priority-high/10 text-priority-high",
    URGENT: "border-priority-urgent/35 bg-priority-urgent/12 text-priority-urgent",
};

interface PriorityBadgeProps {
    priority: TaskPriority;
    /** Низкий приоритет — шум на карточке, поэтому по умолчанию он скрыт. */
    showLow?: boolean;
    className?: string;
}

export function PriorityBadge({ priority, showLow = false, className }: PriorityBadgeProps) {
    if (priority === "LOW" && !showLow) {
        return null;
    }
    return (
        <span
            className={cn(
                "inline-flex items-center rounded-sm border px-1.5 py-0.5",
                "text-[10px] font-semibold uppercase tracking-[0.06em]",
                PRIORITY_VARIANTS[priority],
                className,
            )}
        >
            {PRIORITY_LABELS[priority]}
        </span>
    );
}

const STATUS_VARIANTS: Record<ProjectStatus, string> = {
    PLANNING: "border-line-subtle bg-white/[0.025] text-secondary",
    ACTIVE: "border-accent-border bg-accent-soft text-accent-hover",
    PAUSED: "border-warning/30 bg-warning/10 text-warning",
    COMPLETED: "border-success/30 bg-success/10 text-success",
    ARCHIVED: "border-line-subtle bg-white/[0.02] text-muted",
};

export function ProjectStatusBadge({
    status,
    className,
}: {
    status: ProjectStatus;
    className?: string;
}) {
    return (
        <span
            className={cn(
                "inline-flex items-center rounded-sm border px-1.5 py-0.5 text-[11px] font-medium",
                STATUS_VARIANTS[status],
                className,
            )}
        >
            {PROJECT_STATUS_LABELS[status]}
        </span>
    );
}
