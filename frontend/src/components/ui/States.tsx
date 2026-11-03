import type { ReactNode } from "react";
import { AlertCircle } from "lucide-react";
import { cn } from "@/lib/cn";

export function Skeleton({ className }: { className?: string }) {
    return <div aria-hidden="true" className={cn("animate-pulse rounded-md bg-white/[0.045]", className)} />;
}

interface EmptyStateProps {
    title: string;
    /** Пустое состояние обязано объяснять следующий шаг (раздел 18). */
    description?: string;
    action?: ReactNode;
    icon?: ReactNode;
    className?: string;
}

export function EmptyState({ title, description, action, icon, className }: EmptyStateProps) {
    return (
        <div
            role="status"
            className={cn(
                "flex flex-col items-center justify-center gap-3 rounded-lg bg-surface/35",
                "px-6 py-12 text-center",
                className,
            )}
        >
            {icon && <div className="brand-empty-symbol">{icon}</div>}
            <div className="flex flex-col gap-1">
                <p className="text-sm font-medium text-secondary">{title}</p>
                {description && <p className="max-w-sm text-[13px] text-muted">{description}</p>}
            </div>
            {action}
        </div>
    );
}

interface ErrorMessageProps {
    title?: string;
    message: string;
    action?: ReactNode;
    className?: string;
}

export function ErrorMessage({
    title = "Не удалось загрузить данные",
    message,
    action,
    className,
}: ErrorMessageProps) {
    return (
        <div
            role="alert"
            className={cn(
                "flex items-start gap-3 rounded-md border border-danger/30 bg-danger/10 px-4 py-3",
                className,
            )}
        >
            <AlertCircle size={16} className="mt-0.5 shrink-0 text-danger" aria-hidden="true" />
            <div className="flex min-w-0 flex-col gap-1">
                <p className="text-[13px] font-semibold text-danger">{title}</p>
                <p className="text-[13px] break-words text-secondary">{message}</p>
                {action}
            </div>
        </div>
    );
}
