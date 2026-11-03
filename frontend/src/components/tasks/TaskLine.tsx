import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import type { TaskPriority } from "@/lib/types";
import { PriorityBadge, StatusDot } from "@/components/ui/Badge";

interface TaskLineProps {
    /** Цвет проекта: нужен там, где в списке смешаны разные проекты. */
    dotColor?: string;
    taskKey: string;
    title: string;
    stage?: string | null;
    priority?: TaskPriority | null;
    /** Правая колонка: срок, время изменения или другой одиночный факт. */
    meta?: ReactNode;
    onOpen: () => void;
}

/**
 * Строка задачи в любом списке приложения.
 *
 * Колонки фиксированной ширины: два списка рядом должны читаться как одна
 * таблица, а не как два по-разному сжатых столбца. Ключ, стадия, приоритет
 * и правый факт стоят на одних и тех же позициях, поэтому взгляд идёт
 * сверху вниз по колонке, а не ищет её заново в каждой строке.
 */
export function TaskLine({
    dotColor,
    taskKey,
    title,
    stage,
    priority,
    meta,
    onOpen,
}: TaskLineProps) {
    return (
        <button
            type="button"
            onClick={onOpen}
            className={cn(
                "grid w-full min-w-0 grid-cols-[1fr_auto] items-center gap-x-3 gap-y-2 rounded-md px-2.5 py-3 text-left sm:flex sm:py-2",
                "transition-colors duration-[var(--duration-fast)] ease-[var(--ease-standard)]",
                "hover:bg-hover",
            )}
        >
            <span className="col-start-1 row-start-1 flex min-w-0 shrink-0 items-center gap-2 truncate font-mono text-[11px] text-muted sm:w-20">
                {dotColor ? <StatusDot color={dotColor} /> : null}
                {taskKey}
            </span>
            <span className="col-span-2 row-start-2 line-clamp-2 min-w-0 flex-1 text-[14px] break-words text-secondary sm:line-clamp-none sm:truncate sm:text-[13px]">{title}</span>
            <span className="col-start-1 row-start-3 min-w-0 truncate text-[11px] text-muted sm:w-24 sm:shrink-0 sm:text-right">
                {stage ?? ""}
            </span>
            <span className="col-start-2 row-start-3 flex justify-end sm:w-[5.5rem] sm:shrink-0">
                {priority ? <PriorityBadge priority={priority} /> : null}
            </span>
            <span className="col-start-2 row-start-1 text-right text-[11px] text-muted sm:w-24 sm:shrink-0">{meta}</span>
        </button>
    );
}
