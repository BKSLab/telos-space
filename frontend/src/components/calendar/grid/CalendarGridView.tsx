import { useRef, type TouchEvent } from "react";
import type { GridScale } from "@/lib/calendarGrid";
import type { CalendarGridMilestone, CalendarGridTask } from "@/lib/types";
import { GridDay } from "./GridDay";
import type { GridEnv } from "./GridItems";
import { GridMonth } from "./GridMonth";
import { GridWeek } from "./GridWeek";
import { GridYear } from "./GridYear";

export type { GridEnv, GridDragData } from "./GridItems";

interface CalendarGridViewProps {
    scale: GridScale;
    date: string;
    tasks: CalendarGridTask[];
    milestones: CalendarGridMilestone[];
    env: GridEnv;
    isPhone: boolean;
    /** Свайп влево/вправо на телефоне — следующий/предыдущий период. */
    onSwipe?: (delta: number) => void;
}

const SWIPE_MIN = 60;

/**
 * Сетка календаря — общая для проекта и «Моего календаря». Перетаскивание работает внутри
 * DndContext страницы: клетки дней — приёмники `calendar-day:<дата>`, карточки несут
 * GridDragData (задача и день, за который её взяли).
 */
export function CalendarGridView({ scale, date, tasks, milestones, env, isPhone, onSwipe }: CalendarGridViewProps) {
    const touch = useRef<{ x: number; y: number } | null>(null);
    const swipeHandlers =
        isPhone && onSwipe
            ? {
                  onTouchStart: (event: TouchEvent) => {
                      if (event.touches.length !== 1 || (event.target as HTMLElement).closest("button, input, select, textarea, [data-grid-item]")) {
                          touch.current = null;
                          return;
                      }
                      const point = event.touches[0];
                      touch.current = point ? { x: point.clientX, y: point.clientY } : null;
                  },
                  onTouchCancel: () => { touch.current = null; },
                  onTouchEnd: (event: TouchEvent) => {
                      const start = touch.current;
                      const point = event.changedTouches[0];
                      touch.current = null;
                      if (!start || !point) return;
                      const dx = point.clientX - start.x;
                      const dy = point.clientY - start.y;
                      if (Math.abs(dx) >= SWIPE_MIN && Math.abs(dx) > Math.abs(dy) * 1.5) onSwipe(dx < 0 ? 1 : -1);
                  },
              }
            : {};

    return (
        <div className={isPhone ? "min-h-full" : "h-full min-h-0"} data-calendar-grid={scale} {...swipeHandlers}>
            {scale === "day" && <GridDay date={date} tasks={tasks} milestones={milestones} env={env} />}
            {scale === "week" && <GridWeek date={date} tasks={tasks} milestones={milestones} env={env} isPhone={isPhone} />}
            {scale === "month" && <GridMonth date={date} tasks={tasks} milestones={milestones} env={env} isPhone={isPhone} />}
            {scale === "year" && <GridYear date={date} tasks={tasks} milestones={milestones} env={env} />}
        </div>
    );
}
