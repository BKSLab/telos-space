import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { WandSparkles } from "lucide-react";
import { api, endpoints } from "@/lib/api";
import type { RiskInput } from "@/lib/risks";
import { Button } from "@/components/ui/Button";
import { ErrorMessage } from "@/components/ui/States";

type Field = "description" | "mitigation_plan" | "response_plan";
export function RiskFieldAssistant({ projectId, field, draft, onAccept, disabled }: { projectId: number; field: Field; draft: RiskInput; onAccept: (text: string) => void; disabled?: boolean }) {
    const [proposal, setProposal] = useState<string | null>(null);
    const generate = useMutation({
        mutationFn: () => api.post<{ field: Field; text: string; warnings: string[] }>(endpoints.projectRiskFieldSuggestion(projectId), { ...draft, field }),
        onSuccess: result => setProposal(result.text),
    });
    return <div className="flex flex-wrap items-center gap-2">
        <Button type="button" size="sm" variant="ai" disabled={disabled || generate.isPending} onClick={() => generate.mutate()}>
            <WandSparkles size={13} />{generate.isPending ? "Формулируем…" : "Помочь сформулировать"}
        </Button>
        {generate.error && <ErrorMessage message={(generate.error as Error).message} />}
        {proposal && <div className="w-full rounded-control border border-accent-border bg-accent-soft p-3">
            <p className="whitespace-pre-wrap text-[12px] text-secondary">{proposal}</p>
            <div className="mt-2 flex flex-wrap justify-end gap-2">
                <Button type="button" size="sm" onClick={() => setProposal(null)}>Отклонить</Button>
                <Button type="button" size="sm" variant="primary" onClick={() => { onAccept(proposal); setProposal(null); }}>Принять формулировку</Button>
            </div>
        </div>}
    </div>;
}
