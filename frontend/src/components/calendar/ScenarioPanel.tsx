import { AlertTriangle, ArrowRight, Beaker, Check, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/Button";
import type { ScenarioPreview } from "@/lib/types";

function datesLabel(startDate: string | null, dueDate: string | null): string {
    if (startDate === null && dueDate === null) return "без дат";
    if (startDate === null) return dueDate ?? "без дат";
    return `${startDate} — ${dueDate ?? "—"}`;
}

interface ScenarioPanelProps {
    preview: ScenarioPreview | null;
    isPreviewing: boolean;
    isApplying: boolean;
    onApply: () => void;
    onCancel: () => void;
}

/** Явно отделяет несохранённый proposed state от текущего плана. */
export function ScenarioPanel({
    preview,
    isPreviewing,
    isApplying,
    onApply,
    onCancel,
}: ScenarioPanelProps) {
    return (
        <aside className="material-glass rounded-[var(--radius-panel)] border border-ai-border p-3 shadow-panel">
            <div className="mb-2 flex items-center gap-2">
                <Beaker size={14} className="text-purple" aria-hidden="true" />
                <div className="min-w-0 flex-1">
                    <h3 className="text-[11px] font-semibold tracking-[0.06em] text-purple uppercase">
                        Сценарий · Proposed
                    </h3>
                    <p className="text-[10px] text-muted">Изменения ещё не сохранены</p>
                </div>
                {isPreviewing && <span className="text-[10px] text-muted">Расчёт…</span>}
            </div>

            {!preview && !isPreviewing && (
                <p className="py-2 text-[11px] text-muted">
                    Переместите или измените даты задачи, чтобы рассчитать последствия.
                </p>
            )}

            {preview && (
                <>
                    <div className="flex flex-col gap-1 lg:max-h-64 lg:overflow-y-auto">
                        {preview.changes.map((change) => (
                            <div
                                key={change.task_id}
                                className="rounded-[var(--radius-control)] bg-surface-2 px-2 py-1.5"
                            >
                                <div className="flex items-center gap-1.5 text-[10px]">
                                    <span className="font-mono text-accent">{change.task_key}</span>
                                    <span className="min-w-0 flex-1 truncate text-primary">
                                        {change.task_title}
                                    </span>
                                    <span className="text-[9px] text-purple">
                                        {change.source === "CASCADE" ? "следствие" : "прямое"}
                                    </span>
                                </div>
                                <div className="mt-1 flex items-center gap-1 font-mono text-[9px] text-muted">
                                    <span className="truncate">
                                        {datesLabel(
                                            change.current.start_date,
                                            change.current.due_date,
                                        )}
                                    </span>
                                    <ArrowRight size={9} className="shrink-0 text-purple" />
                                    <span className="truncate text-purple">
                                        {datesLabel(
                                            change.proposed.start_date,
                                            change.proposed.due_date,
                                        )}
                                    </span>
                                </div>
                                {change.reasons.map((reason, index) => (
                                    <p
                                        key={`${reason.code}:${index}`}
                                        className="mt-1 text-[9px] text-warning"
                                    >
                                        {reason.message}
                                    </p>
                                ))}
                            </div>
                        ))}
                    </div>
                    {preview.conflicts.map((conflict) => (
                        <div
                            key={`${conflict.code}:${conflict.task_id}`}
                            className="mt-1.5 flex gap-1.5 rounded-md border border-danger/30 bg-danger/10 px-2 py-1.5 text-[10px] text-danger"
                        >
                            <AlertTriangle size={11} className="mt-0.5 shrink-0" />
                            {conflict.message}
                        </div>
                    ))}
                    {preview.consequences_count > 0 && (
                        <p className="mt-2 text-[10px] text-muted">
                            Каскадных изменений: {preview.consequences_count}
                        </p>
                    )}
                </>
            )}

            <div className="mt-3 flex gap-2">
                <Button
                    variant="ghost"
                    size="sm"
                    icon={<RotateCcw size={12} />}
                    disabled={isApplying}
                    onClick={onCancel}
                    className="flex-1"
                >
                    Отменить
                </Button>
                <Button
                    variant="primary"
                    size="sm"
                    icon={<Check size={12} />}
                    disabled={!preview?.can_apply || isPreviewing || isApplying}
                    onClick={onApply}
                    className="flex-1"
                >
                    {isApplying ? "Применение…" : "Apply"}
                </Button>
            </div>
        </aside>
    );
}
