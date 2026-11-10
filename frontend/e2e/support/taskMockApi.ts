import type { Page } from "@playwright/test";
import type { MyTask, Task, TaskComment, TaskTransferLosses, TaskStatus } from "../../src/lib/types";
import { installMockApi as installBaseApi, mockState as baseState, stamp } from "./mockApi";

const zeroLosses: TaskTransferLosses = { dependencies: 0, document_links: 0, risks: 0, board_stickers: 0, wbs_placements: 0 };
interface TaskAttentionItem {
    task_id: number; revision: number; deadline_revision: number; overdue: boolean;
    is_new: boolean; is_overdue: boolean; is_updated: boolean;
}
const token = (taskId: number): TaskAttentionItem => ({ task_id: taskId, revision: 3, deadline_revision: 1, overdue: false, is_new: true, is_overdue: false, is_updated: false });
interface Report {
    task_id: number; revision: number; body_md: string; author_name: string; created_at: string | null;
    source_comment_id: number | null; source_comment_original_id: number | null; source_author_name: string | null;
}
const emptyReport: Report = { task_id: 200, revision: 0, body_md: "", author_name: "", created_at: null, source_comment_id: null, source_comment_original_id: null, source_author_name: null };

export function mockState() {
    const base = baseState();
    const tasks = base.tasks.slice(0, 2).map((task) => ({ ...task, search_match_source: null, search_title: null, search_excerpt: null, wbs_node_id: null, wbs_position: null, canvas_x: null, canvas_y: null } as Task));
    tasks.push({ ...tasks[0], id: 200, key: "T-1", number: 1, title: "Разобрать личные документы", project_id: null, stage_id: null, status: "NEW", participants: [{ id: 200, user: base.me, role: "REPORTER" }, { id: 201, user: base.me, role: "EXECUTOR" }] });
    return { ...base, tasks, attentionItems: [token(100), token(101)], views: [] as { taskId: number; body: unknown }[], comments: [] as TaskComment[], report: { ...emptyReport }, reportHistory: [] as (typeof emptyReport)[], losses: { ...zeroLosses }, outsider: false };
}
export type MockState = ReturnType<typeof mockState>;

export async function installMockApi(page: Page, state: MockState) {
    await page.route("https://fonts.googleapis.com/**", (route) => route.abort());
    await installBaseApi(page, state as unknown as ReturnType<typeof baseState>);
    await page.routeWebSocket("**/api/v1/users/me/task-attention/ws", (socket) => { socket.onMessage(() => {}); });
    await page.route("**/api/v1/**", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const path = url.pathname.replace("/api/v1", "");
        const method = request.method();
        if (method === "OPTIONS") return route.fallback();
        const headers = { "access-control-allow-origin": request.headers()["origin"] ?? "http://127.0.0.1:4179", "access-control-allow-credentials": "true" };
        const reply = (json: unknown, status = 200) => route.fulfill({ status, headers, ...(status === 204 ? {} : { json }) });
        const body = () => { try { return request.postDataJSON() ?? {}; } catch { return {}; } };
        const record = () => state.writes.push({ method, path, body: body() });
        if (path === "/users/me/tasks") return reply(state.tasks.map((task): MyTask => ({ ...task, context: task.project_id === null ? null : { kind: "PROJECT", id: 1, key: "DEMO", name: state.projects[0].name, color: state.projects[0].color }, state: task.status ?? "IN_PROGRESS", state_label: task.status === "NEW" ? "Новая" : task.status === "DONE" ? "Готово" : "В работе", my_roles: ["REPORTER", "EXECUTOR"] })));
        if (path === "/users/me/task-attention") return reply({ new_tasks: state.attentionItems.filter((item) => item.is_new).length, overdue_tasks: state.attentionItems.filter((item) => item.is_overdue).length, updated_tasks: state.attentionItems.filter((item) => item.is_updated).length, total: state.attentionItems.filter((item) => item.is_new || item.is_overdue || item.is_updated).length, items: state.attentionItems });
        if (path === "/users") return reply([state.me]);
        if (path === "/tasks" && method === "POST") {
            record();
            const created = { ...state.tasks[2], ...body(), id: 201, key: "T-2", number: 2, status: "NEW" as TaskStatus };
            state.tasks.push(created);
            return reply(created, 201);
        }
        const match = /^\/tasks\/(\d+)(\/.*)?$/.exec(path);
        if (!match) return route.fallback();
        const taskId = Number(match[1]);
        const rest = match[2] ?? "";
        const task = state.tasks.find((item) => item.id === taskId)!;
        if (rest === "" && method === "PATCH") { record(); Object.assign(task, body()); return reply(task); }
        if (rest === "/status" && method === "PATCH") { record(); task.status = body().status; return reply(task); }
        if (rest === "/attention") return reply(state.attentionItems.find((item) => item.task_id === taskId) ?? { ...token(taskId), is_new: false });
        if (rest === "/view") {
            record(); state.views.push({ taskId, body: body() });
            const item = state.attentionItems.find((item) => item.task_id === taskId);
            if (item && item.revision === body().revision) Object.assign(item, { is_new: false, is_overdue: false, is_updated: false });
            return reply(null, 204);
        }
        if (rest === "/comments") {
            if (method === "POST") { record(); state.comments.push({ id: 1, task_id: taskId, body_md: body().body_md, author: state.me, author_name: "Тестов Тест", created_at: stamp }); return reply(state.comments[0], 201); }
            return reply(state.comments);
        }
        if (rest === "/report/history") return reply(state.reportHistory);
        if (rest === "/report" || rest === "/report/from-comment") {
            if (method === "POST") {
                record();
                const data = body();
                const comment = rest.endsWith("from-comment") ? state.comments.find((item) => item.id === data.comment_id) : null;
                state.report = { ...state.report, task_id: taskId, revision: state.report.revision + 1, body_md: comment?.body_md ?? data.body_md, author_name: "Тестов Тест", created_at: stamp, source_comment_original_id: comment?.id ?? null, source_comment_id: comment?.id ?? null, source_author_name: comment?.author_name ?? null };
                state.reportHistory.unshift({ ...state.report });
            }
            return reply(state.report);
        }
        if (rest === "/transfer/preview") {
            const personal = url.searchParams.get("personal") === "true";
            return reply({ from_key: task.key, target_kind: personal ? "PERSONAL" : "PROJECT", target_name: personal ? "Разовые задачи" : state.projects[0].name, target_key: personal ? "T" : "DEMO", stage_id: personal ? null : 1, stage_name: personal ? null : "В работе", status: personal ? "NEW" : null, executor: state.me, losses: state.losses, outsiders: state.outsider ? [state.members[1].user] : [], notes: [] });
        }
        if (rest === "/transfer") {
            record(); const personal = body().personal === true; const from = task.key;
            Object.assign(task, { key: personal ? "T-20" : "DEMO-20", number: 20, project_id: personal ? null : 1, stage_id: personal ? null : 1, status: personal ? "NEW" : null, wbs_node_id: null, wbs_position: null, canvas_x: null, canvas_y: null });
            return reply({ task, from_key: from, to_key: task.key, losses: state.losses, notes: [] });
        }
        return route.fallback();
    });
}
