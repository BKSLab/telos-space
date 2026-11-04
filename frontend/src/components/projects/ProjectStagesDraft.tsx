import { Plus, Trash2 } from "lucide-react";
import { Button, IconButton } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { PROJECT_COLORS } from "@/lib/types";
import { validStageDrafts, type StageDraft } from "@/lib/projectStages";
import { newUuid } from "@/lib/uuid";

export function ProjectStagesDraft({ stages, onChange }: {
    stages: StageDraft[]; onChange: (stages: StageDraft[]) => void;
}) {
    const update = (index: number, patch: Partial<StageDraft>) =>
        onChange(stages.map((stage, position) => position === index ? { ...stage, ...patch } : stage));
    const move = (index: number, direction: number) => {
        const next = [...stages];
        [next[index], next[index + direction]] = [next[index + direction], next[index]];
        onChange(next);
    };
    return (
        <section aria-labelledby="project-stages-title" className="flex flex-col gap-3">
            <div>
                <h2 id="project-stages-title" className="text-sm font-semibold text-primary">Стадии канбана</h2>
                <p className="mt-1 text-[12px] text-muted">Настройте колонки под команду или оставьте предложенные. Новые задачи попадают в первую стадию.</p>
            </div>
            {stages.map((stage, index) => (
                <div key={stage.draftId} className="flex flex-wrap items-center gap-2 rounded-[var(--radius-control)] bg-surface-2/60 p-2">
                    <input type="color" aria-label={`Цвет стадии ${index + 1}`} value={stage.color}
                        className="h-8 w-9 cursor-pointer rounded border border-line bg-surface-2"
                        onChange={(event) => update(index, { color: event.target.value })} />
                    <Input aria-label={`Название стадии ${index + 1}`} value={stage.name} maxLength={100}
                        className="min-w-32 flex-1" onChange={(event) => update(index, { name: event.target.value })} />
                    <label className="inline-flex items-center gap-1.5 text-[12px] text-muted">
                        <input type="checkbox" checked={stage.is_done_stage} className="accent-[var(--color-accent)]"
                            onChange={(event) => update(index, { is_done_stage: event.target.checked })} />Завершающая
                    </label>
                    <IconButton label={`Поднять стадию ${index + 1}`} size="sm" disabled={index === 0} onClick={() => move(index, -1)}>↑</IconButton>
                    <IconButton label={`Опустить стадию ${index + 1}`} size="sm" disabled={index === stages.length - 1} onClick={() => move(index, 1)}>↓</IconButton>
                    <IconButton label={`Удалить стадию ${index + 1}`} size="sm" disabled={stages.length === 1}
                        onClick={() => onChange(stages.filter((_, position) => position !== index))}>
                        <Trash2 size={13} />
                    </IconButton>
                </div>
            ))}
            {!validStageDrafts(stages) && stages.length > 0 && <p role="alert" className="text-[12px] text-danger">Укажите разные непустые названия стадий.</p>}
            <Button className="self-start" disabled={stages.length >= 30} onClick={() => onChange([...stages, {
                draftId: newUuid(), name: "", color: PROJECT_COLORS[stages.length % PROJECT_COLORS.length], is_done_stage: false,
            }])}><Plus size={14} />Добавить стадию</Button>
        </section>
    );
}
