import { useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { clampSplitRatio } from "@/lib/ganttView";
import { readStored, writeStored } from "@/lib/useCalendarView";

const STORAGE_KEY = "telos.calendar.project.split";

/**
 * «Рядом»: сетка сверху, шкала снизу, между ними — разделитель, который тянут мышью или
 * стрелками ↑/↓. Доля сетки хранится в браузере. Прокрутка — только внутри панелей.
 */
export function SplitCalendarLayout({ top, bottom }: { top: ReactNode; bottom: ReactNode }) {
    const [ratio, setRatio] = useState(() => clampSplitRatio(readStored<number>(STORAGE_KEY)));
    const rootRef = useRef<HTMLDivElement>(null);

    function commit(next: number) {
        const value = clampSplitRatio(next);
        setRatio(value);
        writeStored(STORAGE_KEY, value);
    }

    function startDrag(event: ReactPointerEvent<HTMLDivElement>) {
        const root = rootRef.current;
        if (!root) return;
        event.preventDefault();
        const rect = root.getBoundingClientRect();
        const move = (moveEvent: PointerEvent) => {
            setRatio(clampSplitRatio((moveEvent.clientY - rect.top) / rect.height));
        };
        const finish = (upEvent: PointerEvent) => {
            window.removeEventListener("pointermove", move);
            commit((upEvent.clientY - rect.top) / rect.height);
        };
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", finish, { once: true });
    }

    function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
        if (event.key === "ArrowUp" || event.key === "ArrowDown") {
            event.preventDefault();
            commit(ratio + (event.key === "ArrowUp" ? -0.05 : 0.05));
        }
    }

    return (
        <div ref={rootRef} className="flex min-h-0 flex-1 flex-col" data-calendar-split="">
            {/* Доли — через flex-basis 0: процентная высота внутри flex-колонки не держится, и сетка распирала панель. */}
            <div className="scrollbar-thin min-h-0 overflow-auto" style={{ flex: `${ratio} 1 0px` }} data-split-pane="grid">
                {top}
            </div>
            <div
                role="separator"
                aria-orientation="horizontal"
                aria-label="Граница между сеткой и шкалой"
                aria-valuemin={20}
                aria-valuemax={80}
                aria-valuenow={Math.round(ratio * 100)}
                tabIndex={0}
                title="Потяните вверх или вниз, чтобы поменять размер сетки и шкалы (или стрелки ↑ ↓)"
                onPointerDown={startDrag}
                onKeyDown={onKeyDown}
                onDoubleClick={() => commit(0.5)}
                className="group relative z-10 flex h-2 shrink-0 cursor-row-resize items-center justify-center border-y border-line bg-surface-2 hover:bg-hover focus-visible:bg-accent/20 focus-visible:outline-none"
            >
                <span className="h-0.5 w-10 rounded-full bg-line-strong group-hover:bg-accent" aria-hidden="true" />
            </div>
            <div className="flex min-h-0 flex-col pt-2" style={{ flex: `${1 - ratio} 1 0px` }} data-split-pane="timeline">
                {bottom}
            </div>
        </div>
    );
}
