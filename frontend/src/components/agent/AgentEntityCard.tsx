import { useNavigate } from "react-router-dom";
import { ArrowUpRight, CalendarDays, Database, Diamond, FileText, FolderTree, Layers, ListTodo, MessageSquare, Paperclip, ShieldAlert, StickyNote, Users, UserRound } from "lucide-react";
import type { KnowledgeEntityType, KnowledgeSource, Project, TaskPriority } from "@/lib/types";
import { PROJECT_STATUS_LABELS } from "@/lib/types";
import { RISK_STATUS_LABELS } from "@/lib/risks";
import { useUiStore } from "@/stores/ui";
import { apiUrl } from "@/lib/api";
import { UserAvatar } from "@/components/users/UserAvatar";
import { PriorityBadge } from "@/components/ui/Badge";
import { chatContextKey, sourceContextTarget, useChatContext } from "@/lib/chatContext";

const TYPES = {
    project: ["Проект", Layers], task: ["Задача", ListTodo], document: ["Документ", FileText], risk: ["Риск", ShieldAlert],
    milestone: ["Веха", Diamond], wbs_node: ["Раздел ИСР", FolderTree], stage: ["Стадия", Layers], sticker: ["Стикер", StickyNote],
    task_report: ["Отчёт по задаче", FileText],
    project_file: ["Файл проекта", Paperclip],
    member: ["Участник", Users], comment: ["Комментарий", MessageSquare], attachment: ["Файл", Paperclip],
    activity: ["История задачи", ListTodo], deadline_change: ["Изменение срока", CalendarDays], analytics_report: ["Отчёт", Database],
} as const;
const ROUTES: Partial<Record<KnowledgeEntityType, string>> = { wbs_node: "structure", stage: "board", sticker: "whiteboard", member: "team", deadline_change: "settings", document: "docs" };
const STATUS: Record<string, string> = { ...PROJECT_STATUS_LABELS, ...RISK_STATUS_LABELS, PLANNED: "Запланирована", ACHIEVED: "Достигнута", CANCELLED: "Отменена", MISSED: "Пропущена" };

export function AgentEntityCard({ source, project }: { source: KnowledgeSource; project: Pick<Project, "id" | "key"> | null }) {
    const navigate = useNavigate();
    const context = useChatContext();
    const target = context && project?.id === context.projectId ? sourceContextTarget(source) : null;
    const [label, Icon] = TYPES[source.entity_type] ?? ["Объект проекта", Database];
    const canOpen = source.task_id != null || project != null;
    const card = source.card;
    const key = card?.key ?? ((source.entity_type === "task" || source.entity_type === "risk") && source.title.includes(" · ") ? source.title.split(" · ")[0] : null);
    const title = key && source.title.startsWith(`${key} · `) ? source.title.slice(key.length + 3) : source.title;
    const open = () => {
        if (context && target) { context.select(target); return; }
        if (source.entity_type === "risk" && project) { useUiStore.getState().setSelectedRisk({ projectId: project.id, riskId: source.entity_id }); return; }
        if (source.entity_type === "project_file" && project) { navigate(`/projects/${project.key}/docs?file=${source.entity_id}`); return; }
        if (source.entity_type === "task_report" && source.task_id != null) { navigate(`/my-tasks?task=${source.task_id}&task_tab=report`); return; }
        if (source.task_id != null) { useUiStore.getState().setSelectedTaskId(source.task_id); return; }
        if (!project) return;
        if (source.document_slug) { navigate(`/projects/${project.key}/docs/${source.document_slug}`); return; }
        if (source.entity_type === "milestone") { navigate(`/projects/${project.key}/calendar?milestone=${source.entity_id}`); return; }
        const route = ROUTES[source.entity_type];
        navigate(`/projects/${project.key}${route ? `/${route}` : ""}${source.entity_type === "wbs_node" ? `?node=${source.entity_id}` : ""}`);
    };
    const priority = card?.priority && ["LOW", "MEDIUM", "HIGH", "URGENT"].includes(card.priority) ? card.priority as TaskPriority : null;
    const date = card?.due_date ? new Date(`${card.due_date.slice(0, 10)}T12:00:00`) : null;
    const Tag = canOpen ? "button" : "div";
    return <Tag type={canOpen ? "button" : undefined} onClick={canOpen ? open : undefined} aria-label={`${label}: ${source.title}`} aria-pressed={target ? Boolean(context?.target && chatContextKey(context.target) === chatContextKey(target)) : undefined} className="brand-entity-card group flex w-full min-w-0 flex-col gap-2.5 rounded-xl border border-transparent p-3 text-left shadow-card transition-colors focus-visible:outline-2 focus-visible:outline-accent">
        <span className="flex items-center gap-2 text-[10px] text-muted"><Icon size={14} className="shrink-0 text-ai-cyan" /><span className="text-ai-violet">{label}</span>{key && <span className="truncate font-mono text-secondary">{key}</span>}{canOpen && <ArrowUpRight size={13} className="ml-auto shrink-0 text-muted group-hover:text-accent" />}</span>
        <span className="flex min-w-0 items-center gap-2.5">
            {source.entity_type === "member" && <UserAvatar size="sm" avatarUrl={card?.avatar_url ? apiUrl(card.avatar_url) : undefined} hasAvatar={Boolean(card?.avatar_url)} fallbackInitials={title.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join("").toUpperCase()} />}
            <span className="min-w-0">
                <span className="line-clamp-3 text-[13px] leading-snug font-semibold break-words text-primary">{title}</span>
                {source.entity_type === "member" && card?.login && <span className="block text-[11px] text-muted">{card.login}</span>}
                {source.entity_type === "member" && card?.role && <span className="block text-[11px] text-secondary">{card.role === "OWNER" ? "Владелец проекта" : "Участник"}</span>}
            </span>
        </span>
        {(card?.status || priority) && <span className="flex flex-wrap items-center gap-2">{card?.status && <span className="rounded border border-accent/15 bg-accent/8 px-1.5 py-0.5 text-[10px] text-accent">{source.entity_type === "task" ? card.status : STATUS[card.status] ?? card.status}</span>}{priority && <PriorityBadge priority={priority} />}</span>}
        {card?.summary && <span className="line-clamp-2 text-[12px] leading-relaxed break-words text-secondary">{card.summary.replace(/[#*_`]/g, "")}</span>}
        {(card?.assignee || (date && !Number.isNaN(date.valueOf()))) && <span className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line-subtle pt-2 text-[11px] text-muted">
            {card?.assignee && <span className="flex min-w-0 items-center gap-1.5"><UserRound size={12} className="shrink-0" /><span className="truncate">{card.assignee}</span></span>}
            {date && !Number.isNaN(date.valueOf()) && <span className="flex items-center gap-1.5"><CalendarDays size={12} /><time dateTime={card!.due_date!}>{date.toLocaleDateString("ru-RU")}</time></span>}
        </span>}
    </Tag>;
}
