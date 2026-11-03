import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export interface PulseChip {
    id: string;
    label: ReactNode;
    title: string;
    tone?: "danger" | "warn" | "info" | "muted";
    icon?: ReactNode;
    onClick?: () => void;
}

const TONES = {
    danger: "text-danger",
    warn: "text-warning",
    info: "text-accent",
    muted: "text-secondary",
} as const;

/** Пульс периода одной строкой чипов над календарём; нулевые сигналы не показываются. */
export function CalendarPulseChips({ chips }: { chips: PulseChip[] }) {
    if (chips.length === 0) return null;
    return (
        <div className="scrollbar-thin flex min-w-0 flex-nowrap items-center gap-1.5 overflow-x-auto lg:flex-wrap" aria-label="Пульс периода">
            {chips.map((chip) => {
                const className = cn(
                    "flex h-6 shrink-0 items-center gap-1 rounded-full border border-line bg-surface px-2 text-[11px] whitespace-nowrap",
                    TONES[chip.tone ?? "muted"],
                    chip.onClick && "hover:border-line-strong hover:bg-hover",
                );
                return chip.onClick ? (
                    <button key={chip.id} type="button" className={className} title={chip.title} onClick={chip.onClick}>
                        {chip.icon}
                        {chip.label}
                    </button>
                ) : (
                    <span key={chip.id} className={className} title={chip.title}>
                        {chip.icon}
                        {chip.label}
                    </span>
                );
            })}
        </div>
    );
}
