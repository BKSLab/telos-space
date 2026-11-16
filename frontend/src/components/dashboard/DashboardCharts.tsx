import { useId } from "react";
import { cn } from "@/lib/cn";
import "./DashboardVisuals.css";

export interface DistributionSegment {
    id: string | number;
    label: string;
    value: number;
    color: string;
}

/** Сегменты — непересекающиеся фактические количества; цвета не заменяют подписи. */
export function DistributionRing({ label, segments, centerValue, centerLabel, emptyLabel = "Нет данных", className }: {
    label: string;
    segments: DistributionSegment[];
    centerValue: string | number;
    centerLabel: string;
    emptyLabel?: string;
    className?: string;
}) {
    const identity = useId().replace(/[^a-zA-Z0-9-]/g, "");
    const values = segments.map(segment => ({ ...segment, value: Number.isFinite(segment.value) ? Math.max(segment.value, 0) : 0 }));
    const total = values.reduce((sum, segment) => sum + segment.value, 0);
    const circumference = 2 * Math.PI * 65;
    const nonempty = values.filter(segment => segment.value > 0).length;
    const arcs = values.map((segment, index) => {
        const length = total > 0 ? segment.value / total * circumference : 0;
        const start = total > 0 ? values.slice(0, index).reduce((sum, item) => sum + item.value, 0) / total * circumference : 0;
        return { ...segment, index, start, length: Math.max(0, length - (nonempty > 1 ? Math.min(4, length * .2) : 0)) };
    });

    return <div className={cn("dashboard-ring", className)}>
        <div role="img" aria-label={label} className="dashboard-ring-graphic">
            <svg viewBox="0 0 160 160" aria-hidden="true">
                <defs>{values.map((segment, index) => <linearGradient id={`${identity}-${index}`} key={segment.id} x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0%" stopColor={`color-mix(in srgb, ${segment.color} 78%, var(--color-text-primary))`} />
                    <stop offset="100%" stopColor={segment.color} />
                </linearGradient>)}</defs>
                <circle cx="80" cy="80" r="65" fill="none" stroke="var(--color-border)" strokeWidth="14" />
                <circle cx="80" cy="80" r="52" fill="none" stroke="var(--color-border-subtle)" strokeWidth="1" />
                <g transform="rotate(-90 80 80)">{arcs.filter(segment => segment.length > 0).map(segment => <circle key={segment.id}
                    cx="80" cy="80" r="65" fill="none" stroke={`url(#${identity}-${segment.index})`} strokeWidth="14"
                    strokeDasharray={`${segment.length} ${circumference}`} strokeDashoffset={-segment.start} strokeLinecap={nonempty > 1 ? "butt" : "round"} />)}</g>
            </svg>
            <div className="dashboard-ring-center" aria-hidden="true">
                <strong>{total > 0 ? centerValue : "—"}</strong>
                <span>{total > 0 ? centerLabel : emptyLabel}</span>
            </div>
        </div>
        <dl className="dashboard-ring-legend">{values.filter(segment => segment.value > 0).map(segment => <div key={segment.id}>
            <dt><span aria-hidden="true" style={{ backgroundColor: segment.color }} /><span>{segment.label}</span></dt>
            <dd>{segment.value}</dd>
        </div>)}</dl>
    </div>;
}
