import type { HTMLAttributes, ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/cn";

export type CardVariant = "workspace" | "interactive" | "inset" | "plain" | "ai";

interface CardProps extends HTMLAttributes<HTMLDivElement> {
    /** Добавляет hover-подъём. Только для кликабельных карточек. */
    interactive?: boolean;
    variant?: CardVariant;
}

const VARIANTS: Record<CardVariant, string> = {
    workspace: "surface-card border border-line shadow-card",
    interactive: "surface-card border border-line shadow-card",
    inset: "control-surface border border-line-subtle",
    plain: "border border-transparent bg-transparent shadow-none",
    ai: "ai-surface border border-ai-border shadow-card",
};

export function Card({ interactive = false, variant, className, children, ...props }: CardProps) {
    const resolvedVariant = variant ?? (interactive ? "interactive" : "workspace");
    return (
        <div
            className={cn(
                "rounded-[var(--radius-card)]",
                VARIANTS[resolvedVariant],
                interactive &&
                    "transition-[background-color,border-color,box-shadow,transform] " +
                        "duration-[var(--duration-normal)] ease-[var(--ease-standard)] " +
                        "hover:-translate-y-px hover:border-line-strong hover:bg-elevated hover:shadow-elevated " +
                        "active:translate-y-0 active:bg-active motion-reduce:transform-none",
                className,
            )}
            {...props}
        >
            {children}
        </div>
    );
}

interface SectionProps {
    title: string;
    action?: ReactNode;
    children: ReactNode;
    className?: string;
}

export function CollapsibleSection({ title, action, children, className }: SectionProps) {
    return (
        <details className={cn("group/section surface-card min-w-0 rounded-[var(--radius-card)] border border-line", className)}>
            <summary className="touch-target flex cursor-pointer list-none items-center justify-between gap-3 rounded-[var(--radius-card)] px-4 py-3 text-sm font-semibold text-primary hover:bg-hover focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-accent sm:px-5 [&::-webkit-details-marker]:hidden">
                <h2>{title}</h2>
                <ChevronRight size={16} aria-hidden="true" className="shrink-0 text-muted group-open/section:rotate-90" />
            </summary>
            <div className="flex min-w-0 flex-col gap-4 border-t border-line-subtle px-4 py-4 sm:px-5">
                {action && <div className="self-start">{action}</div>}
                {children}
            </div>
        </details>
    );
}

/** Секция рабочего экрана: заголовок, необязательное действие и содержимое. */
export function Section({ title, action, children, className }: SectionProps) {
    return (
        <section className={cn("flex min-w-0 flex-col gap-3", className)}>
            <div className="flex items-center justify-between gap-3">
                <h2 className="text-[13px] font-semibold tracking-[-0.01em] text-secondary">
                    {title}
                </h2>
                {action}
            </div>
            {children}
        </section>
    );
}
