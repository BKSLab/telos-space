import { plural } from "@/lib/plural";
import type { Project, Task, TaskKind, TaskTransferLosses, TaskTransferTarget } from "@/lib/types";

export type TransferKind = TaskKind;
export const TRANSFER_KIND_LABELS: Record<TransferKind, string> = { PROJECT: "В проект", PERSONAL: "Сделать разовой" };
export const TRANSFER_KIND_HINTS: Record<TransferKind, string> = {
    PROJECT: "Задача встанет на доску выбранного проекта и получит его номер",
    PERSONAL: "Задача останется без проекта с общим номером T-…",
};
export function transferDeniedHint(kind: TaskKind, canEdit: boolean, isOwner: boolean): string | undefined {
    if (kind === "PROJECT") return canEdit ? undefined : "Переносить задачи проекта могут только участники его команды";
    return isOwner ? undefined : "Перенести разовую задачу может её постановщик";
}
export function transferOptions<P extends Pick<Project, "id">>(task: Pick<Task, "project_id">, kind: TaskKind, projects: P[]) {
    return { projects: projects.filter(project => project.id !== task.project_id), personal: kind !== "PERSONAL" };
}
export function buildTransferTarget(kind: TransferKind, choice: { projectId: number | null; stageId: number | null; removeOutsiders?: boolean }): TaskTransferTarget | null {
    if (kind === "PERSONAL") return { personal: true };
    if (choice.projectId === null) return null;
    return { project_id: choice.projectId, ...(choice.stageId !== null && { stage_id: choice.stageId }), ...(choice.removeOutsiders && { remove_outsiders: true }) };
}
export function transferTargetQuery(target: TaskTransferTarget): string {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(target)) if (value !== undefined && value !== null) params.set(key, String(value));
    return params.toString();
}
export function transferLossesText(losses: TaskTransferLosses & { wbs_placements?: number }): string {
    const count = (value: number, one: string, few: string, many: string) => value > 0 ? `${value} ${plural(value, one, few, many)}` : null;
    return [count(losses.dependencies, "зависимость", "зависимости", "зависимостей"),
        count(losses.document_links, "связь с документом", "связи с документами", "связей с документами"),
        count(losses.risks, "риск", "риска", "рисков"), count(losses.board_stickers, "стикер доски", "стикера доски", "стикеров доски"),
        count(losses.wbs_placements ?? 0, "размещение в ИСР", "размещения в ИСР", "размещений в ИСР")].filter(Boolean).join(", ");
}
