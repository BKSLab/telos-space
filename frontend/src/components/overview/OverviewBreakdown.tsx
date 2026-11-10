import type { StageBreakdown as StageBreakdownItem } from "@/lib/types";
import { ProgressBar, SegmentedProgress } from "@/components/ui/Progress";
import { StatusDot } from "@/components/ui/Badge";

/** Текущие стадии образуют полное распределение; просрочка в него не входит. */
export function StageBreakdown({ stages }: { stages: StageBreakdownItem[] }) {
    if (stages.length === 0) return null;
    const total = stages.reduce((sum, item) => sum + item.tasks_count, 0);

    return <div className="flex flex-col gap-4">
        <SegmentedProgress className="h-3" segments={stages.map(item => ({ id: item.stage_id, value: item.tasks_count, color: item.color, label: item.stage_name }))} />
        <div className="flex flex-col gap-3.5">
            {stages.map(item => <div key={item.stage_id} className="flex flex-col gap-1.5">
                <div className="flex items-center gap-2 text-[14px]">
                    <StatusDot color={item.color} />
                    <span className="min-w-0 flex-1 break-words text-secondary">{item.stage_name}</span>
                    <span className="font-semibold text-primary tabular-nums">{item.tasks_count}</span>
                    <span className="w-10 text-right text-[12px] text-muted tabular-nums">{total > 0 ? `${Math.round(item.tasks_count / total * 100)}%` : "—"}</span>
                </div>
                <ProgressBar value={total > 0 ? item.tasks_count / total : 0} color={item.color} label={`Доля задач в стадии «${item.stage_name}»`} />
            </div>)}
        </div>
    </div>;
}
