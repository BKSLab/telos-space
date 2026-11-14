import { FileText, Flag, FolderTree, ListTodo, ShieldAlert } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useState } from "react";
import { useUiStore } from "@/stores/ui";
import type { ChatEntity } from "@/lib/projectChat";
import { chatContextKey, entityContextTarget, useChatContext } from "@/lib/chatContext";
import { DocumentDialog } from "@/components/documents/DocumentView";

const ICONS = { TASK: ListTodo, DOCUMENT: FileText, RISK: ShieldAlert, MILESTONE: Flag, WBS_NODE: FolderTree };
const LABELS = { TASK: "Задача", DOCUMENT: "Документ", RISK: "Риск", MILESTONE: "Веха", WBS_NODE: "Раздел структуры" };

export function ChatEntityCard({ entity, projectId }: { entity: ChatEntity; projectId: number }) {
    const navigate = useNavigate();
    const openTask = useUiStore((state) => state.setSelectedTaskId);
    const openRisk = useUiStore((state) => state.setSelectedRisk);
    const context = useChatContext();
    const [documentOpen, setDocumentOpen] = useState(false);
    const target = context?.projectId === projectId ? entityContextTarget(entity) : null;
    const Icon = ICONS[entity.entity_type];
    const open = () => {
        if (!entity.available) return;
        if (context && target) {
            context.select(target, { open: target.type !== "task" || context.desktop });
            if (target.type === "task") openTask(target.id);
            return;
        }
        if (entity.entity_type === "DOCUMENT" && entity.document_ref) setDocumentOpen(true);
        else if (entity.entity_type === "TASK" && entity.entity_id !== null) openTask(entity.entity_id);
        else if (entity.entity_type === "RISK" && entity.entity_id !== null) openRisk({ projectId, riskId: entity.entity_id });
        else if (entity.href) navigate(entity.href);
    };
    return <><button type="button" disabled={!entity.available} onClick={open} aria-label={`${LABELS[entity.entity_type]}: ${entity.title}`} aria-pressed={target ? Boolean(context?.target && chatContextKey(context.target) === chatContextKey(target)) : undefined} className="surface-card flex min-w-0 max-w-sm items-start gap-3 rounded-lg border border-line px-3.5 py-3 text-left shadow-card transition-colors hover:border-line-strong hover:bg-hover aria-pressed:border-accent/50 aria-pressed:bg-accent/5 focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 disabled:opacity-55">
        <Icon size={16} className="mt-0.5 shrink-0 text-secondary" aria-hidden="true" />
        <span className="min-w-0 [overflow-wrap:anywhere]">
            <span className="block text-[10px] text-muted">{entity.subtitle ?? LABELS[entity.entity_type]}{entity.status && ` · ${entity.status}`}</span>
            <span className="mt-1 line-clamp-2 text-[13px] font-medium leading-relaxed text-primary">{entity.title}</span>
        </span>
    </button>{documentOpen && entity.document_ref && <DocumentDialog documentRef={entity.document_ref} expectedProjectId={projectId} onClose={() => setDocumentOpen(false)} />}</>;
}
