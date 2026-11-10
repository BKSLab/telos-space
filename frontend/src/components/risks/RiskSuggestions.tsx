import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Sparkles, X } from "lucide-react";
import { api, endpoints } from "@/lib/api";
import { riskInput, type RiskSuggestion } from "@/lib/risks";
import { Button, IconButton } from "@/components/ui/Button";
import { ErrorMessage } from "@/components/ui/States";
import { AiOperationStatus } from "@/components/ui/AiOperationStatus";
import { CreateRiskDialog } from "@/components/risks/CreateRiskDialog";

export function RiskSuggestions({ projectId }: { projectId: number }) {
    const [suggestions, setSuggestions] = useState<RiskSuggestion[] | null>(null);
    const [selected, setSelected] = useState<RiskSuggestion | null>(null);
    const generate = useMutation({
        mutationFn: () => api.post<{ suggestions: RiskSuggestion[] }>(endpoints.projectRiskSuggestions(projectId)),
        onSuccess: (data) => setSuggestions(data.suggestions),
    });
    return (
        <section aria-label="Предложения рисков AI" className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-[11px] text-muted">AI может предложить риски по материалам проекта.</p>
                <Button variant="ghost" size="sm" icon={<Sparkles size={13} className="text-ai" />} disabled={generate.isPending} onClick={() => generate.mutate()}>{generate.isPending ? "Изучаю проект…" : "Предложить риски"}</Button>
            </div>
            {generate.isPending && <AiOperationStatus message="Арис изучает проект и предлагает риски…"
                description="Анализирует задачи, сроки, комментарии и документы." />}
            {generate.error && <ErrorMessage title="Не удалось подготовить предложения" message={generate.error.message} />}
            {suggestions && <div className="rounded-card border border-ai-border/30 bg-ai-soft/30 px-4 py-3">
                <div className="mb-2 flex items-center justify-between gap-2">
                    <h2 className="text-[12px] font-medium text-primary">Возможные риски · требуют вашей проверки</h2>
                    <IconButton label="Скрыть предложения" onClick={() => setSuggestions(null)}><X size={14} /></IconButton>
                </div>
                {suggestions.length === 0 && <p role="status" className="text-[12px] text-muted">Новых обоснованных рисков не предложено.</p>}
                <ul className="divide-y divide-line-subtle">
                    {suggestions.map((item, index) => <li key={index} className="space-y-2 py-3">
                        <h3 className="text-[13px] font-medium text-primary">{item.title}</h3>
                        <p className="text-[12px] text-secondary">{item.description}</p>
                        <ul className="ml-4 list-disc space-y-1 text-[11px] text-muted">{item.evidence.map((reason, reasonIndex) => <li key={reasonIndex}>{reason}</li>)}</ul>
                        <div className="flex flex-wrap gap-2">
                            <Button size="sm" onClick={() => setSelected(item)}>Проверить и создать</Button>
                            <Button size="sm" variant="ghost" onClick={() => setSuggestions((current) => current?.filter((candidate) => candidate !== item) ?? [])}>Отклонить</Button>
                        </div>
                    </li>)}
                </ul>
            </div>}
            {selected && <CreateRiskDialog
                projectId={projectId} initial={riskInput({ ...selected, source: "AI_SUGGESTED" })}
                onClose={() => setSelected(null)}
                onCreated={() => setSuggestions((current) => current?.filter((item) => item !== selected) ?? [])}
            />}
        </section>
    );
}

