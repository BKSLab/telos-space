import { AlertTriangle, CalendarDays } from "lucide-react";
import { cn } from "@/lib/cn";
import { BrandIcon } from "@/components/ui/BrandIcon";
import { daysUntil, dueTone, formatDayMonth, formatFullDate } from "@/lib/dates";

interface DueDateProps {
    value: string | null;
    isDone?: boolean;
    className?: string;
}

/**
 * Срок задачи. Просрочка обозначается иконкой и подписью, а не заливкой
 * карточки, и никогда не передаётся одним лишь цветом (разделы 9 и 16).
 */
export function DueDate({ value, isDone = false, className }: DueDateProps) {
    if (value === null) {
        return null;
    }
    const tone = dueTone(value, isDone);
    const days = daysUntil(value);
    const isOverdue = tone === "danger";
    const suffix =
        isOverdue && days !== null
            ? ` · просрочено на ${Math.abs(days)} дн`
            : tone === "warning" && days === 0
              ? " · сегодня"
              : "";

    return (
        <span
            className={cn("inline-flex shrink-0 items-center gap-1 whitespace-nowrap text-[11px]",
                isDone ? "text-muted" : isOverdue ? "text-danger" : "text-accent", className)}
            title={`Срок: ${formatFullDate(value)}${isOverdue ? " (просрочен)" : ""}`}
        >
            {isOverdue ? (
                <AlertTriangle size={12} className="shrink-0" aria-hidden="true" />
            ) : isDone ? (
                <CalendarDays size={12} className="shrink-0" aria-hidden="true" />
            ) : (
                <BrandIcon icon={CalendarDays} />
            )}
            <span className={cn("font-mono", !isDone && !isOverdue && "brand-accent-text")}>{formatDayMonth(value)}</span>
            {suffix && <span className="sr-only">{suffix}</span>}
        </span>
    );
}
