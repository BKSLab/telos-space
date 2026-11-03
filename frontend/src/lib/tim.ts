/** Арис — помощник личного трекера. */
import type { TimPageContext } from "@/lib/types";

/** Имя агента для людей: автор ответов, заголовок окна, кнопка. */
export const TIM_NAME = "Арис";
export const TIM_PERSONA = "Арис — проектный менеджер";
/** Подсказка в поле ввода чата проекта: без неё обращение к Арису не найти. */
export const TIM_CHAT_HINT = "Начните сообщение с «Арис, …», чтобы обратиться к проектному менеджеру";

/**
 * Адресовано ли сообщение Арису: после trim начинается со слова «Арис» или «ARIS»,
 * за которым конец строки либо не буква, не цифра и не подчёркивание. Только для подсказки —
 * решает сервер.
 */
export function isForTim(text: string | null | undefined): boolean {
    return /^(?:арис|aris)(?:$|[^\p{L}\p{N}_])/iu.test((text ?? "").trim());
}

/** Разбор адреса страницы в контекст для Ариса: проект, доска и открытая задача. */
export function pageContext(pathname: string, search: string, openTaskId: number | null, selection: { boardId: number; objects: { type: "shape" | "sticker"; id: number }[] } | null = null): TimPageContext {
    const project = /^\/projects\/([^/]+)/.exec(pathname)?.[1];
    const params = new URLSearchParams(search);
    const linkedTask = Number(params.get("task"));
    const requestedBoard = Number(params.get("board"));
    const onBoard = pathname === "/boards" || /^\/projects\/[^/]+\/whiteboard(?:\/|$)/.test(pathname);
    const taskId = openTaskId ?? (Number.isSafeInteger(linkedTask) && linkedTask > 0 ? linkedTask : null);
    let projectKey: string | null = null;
    if (project && project !== "new") {
        try { projectKey = decodeURIComponent(project).slice(0, 32); }
        catch { projectKey = project.slice(0, 32); }
    }
    const selected = onBoard && Number.isSafeInteger(requestedBoard) && requestedBoard > 0 && selection?.boardId === requestedBoard
        && selection.objects.length > 0 && selection.objects.length <= 200
        && selection.objects.every(item => (item.type === "shape" || item.type === "sticker") && Number.isSafeInteger(item.id) && item.id > 0)
        ? selection.objects.map(item => ({ type: item.type, id: item.id })) : [];
    return {
        ...(selected.length ? { board_selection: selected } : {}),
        ...(onBoard && Number.isSafeInteger(requestedBoard) && requestedBoard > 0 ? { board_id: requestedBoard } : {}),
        route: pathname.slice(0, 500),
        project_key: projectKey,
        task_id: taskId,
    };
}

/** Строка «что видит Арис» для шапки виджета: «Проект DEMO · задача DEMO-12». */
export function describePageContext(
    context: TimPageContext,
    names: { project?: string | null; taskKey?: string | null } = {},
): string {
    const parts: string[] = [];
    if (context.project_key) parts.push(`Проект ${names.project ?? context.project_key}`);
    if (context.board_id) parts.push(`Доска #${context.board_id}`);
    if (context.board_id && context.board_selection?.length) parts.push(`выделено: ${context.board_selection.length}`);
    if (context.task_id) parts.push(`${parts.length ? "задача" : "Задача"} ${names.taskKey ?? `#${context.task_id}`}`);
    return parts.join(" · ");
}
