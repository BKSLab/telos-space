import type { AgentToolRun, KnowledgeEntityType, KnowledgeSource } from "@/lib/types";

const KINDS: Record<string, KnowledgeEntityType> = { task: "task", document: "document", project_file: "project_file", risk: "risk", milestone: "milestone", node: "wbs_node", stage: "stage", project: "project", sticker: "sticker", member: "member", attachment: "attachment", comment: "comment" };
const text = (value: unknown): string | null => typeof value === "string" && value.trim() ? value : null;
const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};

/** Старые результаты действий тоже отображаются карточками, без миграции истории. */
export function actionSources(action: AgentToolRun): KnowledgeSource[] {
    const result = action.status === "pending" || action.status === "rejected" ? record(action.result.preview) : action.result;
    return Object.entries(KINDS).flatMap(([key, kind]) => {
        const data = record(result[key]);
        const id = Number(data.id ?? action.arguments[`${key}_id`]);
        const user = record(data.user);
        const title = text(data.title) ?? text(data.name) ?? text(data.original_name) ?? text(data.body) ?? text(data.body_md)
            ?? (Object.keys(user).length ? [user.last_name, user.first_name].filter(Boolean).join(" ") || text(user.username) : null);
        if (!title || !Number.isSafeInteger(id) || id <= 0) return [];
        const taskId = kind === "task" ? id : Number(data.task_id) || null;
        const stage = record(data.stage);
        return [{ source_id: `${kind}:${id}`, entity_type: kind, entity_id: id, title, excerpt: null, score: null,
            task_id: taskId, document_slug: text(data.slug),
            card: { key: text(data.key) ?? (kind === "risk" ? `RISK-${id}` : null), status: text(data.stage_name) ?? text(stage.name) ?? text(data.status), priority: text(data.priority), assignee: text(data.assignee), due_date: text(data.due_date), summary: text(data.description_md)?.slice(0, 240) ?? null },
        }];
    });
}
