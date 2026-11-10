import { Loader2 } from "lucide-react";
import { cn } from "@/lib/cn";

interface AiOperationStatusProps {
    message: string;
    description?: string;
    className?: string;
}

/** Ожидание прямого AI-запроса из интерфейса, пока операция остаётся pending. */
export function AiOperationStatus({ message, description, className }: AiOperationStatusProps) {
    return <div role="status" aria-live="polite" aria-atomic="true"
        className={cn("brand-ai-progress flex w-full min-w-0 items-start gap-2.5 rounded-control bg-ai-soft px-3 py-2 text-[13px] leading-relaxed", className)}>
        <Loader2 size={15} className="mt-0.5 shrink-0 animate-spin motion-reduce:animate-none" aria-hidden="true" />
        <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">
            <span className="block">{message}</span>
            {description && <span className="mt-0.5 block text-[12px] text-secondary">{description}</span>}
        </span>
    </div>;
}
