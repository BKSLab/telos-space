import type { Page } from "@playwright/test";
import type { Task } from "../../src/lib/types";
import { installMockApi as installBaseApi, mockState as baseState, stamp } from "./mockApi";

export { stamp };
const iso = (offset: number) => {
    const date = new Date();
    date.setDate(date.getDate() + offset);
    return date.toISOString().slice(0, 10);
};

interface ScheduleDependency {
    id: number; project_id: number; predecessor_task_id: number; successor_task_id: number;
    dependency_type: "FINISH_TO_START"; lag_days: number; created_at: string; is_critical?: boolean;
}

export function mockState() {
    const base = baseState();
    base.projects[0] = { ...base.projects[0], name: "Запуск портала заказов", start_date: iso(-30), due_date: iso(45) };
    const stages = [
        { id: 1, project_id: 1, name: "Бэклог", order_index: 0, color: "#818b98", is_done_stage: false },
        { id: 2, project_id: 1, name: "В работе", order_index: 1, color: "#58a6ff", is_done_stage: false },
        { id: 3, project_id: 1, name: "Проверка", order_index: 2, color: "#d29922", is_done_stage: false },
        { id: 4, project_id: 1, name: "Готово", order_index: 3, color: "#3fb950", is_done_stage: true },
    ];
    const tasks = base.tasks.map((task, index) => ({
        ...task, stage_id: index % 4 + 1,
        search_match_source: null, search_title: null, search_excerpt: null,
        assignee: index % 3 === 0 ? "Иванова Анна" : index % 3 === 1 ? "Демов Иван" : "Тестов Тест",
        start_date: iso(index - 6), due_date: iso(index * 2 - 4),
        wbs_node_id: index < 6 ? 1 : 2, wbs_position: index * 1000, canvas_x: null, canvas_y: null,
    } as Task));
    return {
        ...base, tasks, stages, icsIssued: false,
        wbs: [{ id: 1, parent_id: null, title: "Разработка", position: 1000 }, { id: 2, parent_id: 1, title: "Проверка и запуск", position: 1000 }],
        schedule: {
            progress: {} as Record<number, number | null>, critical: [] as number[], dependencies: [] as ScheduleDependency[],
            dependencyError: null as string | null, cascade: [] as { task_id: number; due_date: string }[],
            baseline: { scheduled_count: 12, changed_count: 5 },
        },
    };
}
type MockState = ReturnType<typeof mockState>;

export async function installMockApi(page: Page, state: MockState) {
    await page.route("https://fonts.googleapis.com/**", (route) => route.abort());
    await installBaseApi(page, state as unknown as ReturnType<typeof baseState>);
    await page.route("**/api/v1/**", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const path = url.pathname.replace("/api/v1", "");
        const method = request.method();
        if (method === "OPTIONS") return route.fallback();
        const headers = { "access-control-allow-origin": request.headers()["origin"] ?? "http://127.0.0.1:4179", "access-control-allow-credentials": "true" };
        const reply = (json: unknown, status = 200) => route.fulfill({ status, headers, json });
        const body = () => { try { return request.postDataJSON() ?? {}; } catch { return {}; } };
        if (path === "/calendar/holidays") return reply({ year: Number(url.searchParams.get("year")), days: [] });
        if (path === "/users/me/calendar/ics-token") {
            if (method !== "GET") {
                state.writes.push({ method, path, body: body() });
                state.icsIssued = method === "POST";
            }
            if (method === "DELETE") return route.fulfill({ status: 204, headers });
            return reply({ exists: state.icsIssued, created_at: state.icsIssued ? stamp : null, ...(method === "POST" && { url: "http://tracker.test/api/v1/ics/cal_demo.ics" }) });
        }
        if (path === "/users/me/calendar/unscheduled") return reply({ items: [], next_cursor: null });
        if (path === "/users/me/calendar") return reply({ tasks: [
            { ...calendarTask(state.tasks[0]), source: { kind: "PROJECT", id: 1, key: "DEMO", name: state.projects[0].name, color: state.projects[0].color }, state: "IN_PROGRESS", state_label: "В работе" },
            { ...calendarTask(state.tasks[1]), id: 200, key: "T-1", title: "Личная подготовка к поездке", wbs_node_id: null, stage_id: null, start_date: iso(0), due_date: iso(1), source: { kind: "PERSONAL", id: null, key: null, name: null, color: null }, state: "NEW", state_label: "Новая" },
        ], milestones: [], summary: { overdue: 1, due_soon: 1, unscheduled: 0 }, truncated: false });
        if (path === "/portfolio/calendar") return reply({ projects: [{ ...state.projects[0], completion_rate: 0.25, total_tasks: 12, overdue_tasks: 2, milestones: [] }] });
        const task = /^\/tasks\/(\d+)$/.exec(path);
        if (task && method === "PATCH") {
            const data = body();
            state.writes.push({ method, path, body: data });
            const found = state.tasks.find((item) => item.id === Number(task[1]));
            Object.assign(found ?? {}, data);
            return reply(found);
        }
        const match = /^\/projects\/(\d+)(\/.*)$/.exec(path);
        if (!match) return route.fallback();
        const projectId = Number(match[1]);
        const rest = match[2];
        if (method !== "GET" && ["/task-dependencies", "/baseline", "/calendar/scenarios/preview", "/calendar/scenarios/apply"].includes(rest)) {
            state.writes.push({ method, path, body: body() });
        }
        const schedule = handleSchedule(state, projectId, rest, method, url, body);
        if (schedule) return reply(schedule.json, schedule.status);
        if (rest === "/calendar/workload") return reply(projectWorkload(state, url.searchParams.get("date_from") ?? iso(-14), url.searchParams.get("date_to") ?? iso(30)));
        if (rest === "/calendar/unscheduled") return reply({ items: [], next_cursor: null });
        if (rest === "/calendar") return reply({
            range: { date_from: url.searchParams.get("date_from"), date_to: url.searchParams.get("date_to"), today: iso(0) },
            project: { start_date: state.projects[0].start_date, due_date: state.projects[0].due_date },
            tasks: state.tasks.map(calendarTask).map((item) => ({ ...item, progress: state.schedule.progress[item.id] ?? (item.is_done ? 100 : null), is_critical: state.schedule.critical.includes(item.id) })),
            stages: state.stages, wbs_nodes: state.wbs, assignees: ["Иванова Анна", "Демов Иван", "Тестов Тест"],
            summary: { overdue: 2, due_soon: 1, unscheduled: 0, drifted: 0, dependency_risks: 0 },
            recent_changes: [], dependencies: state.schedule.dependencies,
            milestones: [{ id: 1, title: "Пилот", due_date: iso(14), status: "PLANNED", wbs_node_id: 2, description_md: null, is_system: false }],
        });
        if (rest === "/milestones") return reply([]);
        return route.fallback();
    });
}

function calendarTask(task: Task) {
    return {
        id: task.id, key: task.key, title: task.title, start_date: task.start_date, due_date: task.due_date,
        baseline_start_date: null, baseline_due_date: null, drift_days: null, stage_id: task.stage_id,
        wbs_node_id: task.wbs_node_id, priority: task.priority, assignee: task.assignee, is_done: task.stage_id === 4,
        is_overdue: task.due_date !== null && task.due_date < iso(0) && task.stage_id !== 4,
        is_due_soon: false, risk_level: null, risk_reasons: [], updated_at: stamp,
    };
}

function projectWorkload(state: MockState, dateFrom: string, dateTo: string) {
    const people = ["Иванова Анна", "Демов Иван", "Тестов Тест"].map((name, index) => {
        const days = [];
        for (let date = dateFrom; date <= dateTo;) {
            const value = new Date(`${date}T00:00:00Z`);
            if (value.getUTCDay() !== 0 && value.getUTCDay() !== 6) {
                const keys = state.tasks.filter((task) => task.assignee === name && task.stage_id !== 4 && (task.start_date ?? "") <= date && date <= (task.due_date ?? "")).map((task) => task.key);
                const other = index === 0 && date >= iso(0) && date <= iso(6) ? 2 : 0;
                if (keys.length + other) days.push({ date, project_tasks: keys.length, project_keys: keys, other_tasks: other, other_keys: other ? ["WMS-7"] : [], level: Math.min(keys.length + other, 3) });
            }
            value.setUTCDate(value.getUTCDate() + 1);
            date = value.toISOString().slice(0, 10);
        }
        return { user_id: index + 1, name, days, other_tasks: [], other_hidden: index === 0 ? 1 : 0 };
    });
    return { date_from: dateFrom, date_to: dateTo, people, truncated: false };
}

function handleSchedule(state: MockState, projectId: number, rest: string, method: string, url: URL, body: () => unknown): { json: unknown; status?: number } | null {
    const schedule = state.schedule;
    if (rest === "/task-dependencies" && method === "POST") {
        if (schedule.dependencyError) return { json: { detail: schedule.dependencyError }, status: 409 };
        const data = body() as { predecessor_task_id: number; successor_task_id: number; lag_days: number };
        const created: ScheduleDependency = { id: 900 + schedule.dependencies.length, project_id: projectId, dependency_type: "FINISH_TO_START", created_at: stamp, ...data };
        schedule.dependencies.push(created);
        return { json: created, status: 201 };
    }
    if (rest === "/task-dependencies") return { json: schedule.dependencies };
    if (rest === "/baseline" && method === "POST") {
        const dryRun = url.searchParams.get("dry_run") === "true";
        return { json: { dry_run: dryRun, ...schedule.baseline, task_ids: dryRun ? [] : state.tasks.slice(0, schedule.baseline.changed_count).map((task) => task.id) } };
    }
    if (rest === "/calendar/scenarios/preview" && method === "POST") {
        const direct = (body() as { changes: { task_id: number; start_date: string | null; due_date: string | null }[] }).changes;
        const row = (taskId: number, start: string | null, due: string | null, source: "DIRECT" | "CASCADE") => {
            const task = state.tasks.find((item) => item.id === taskId) ?? state.tasks[0];
            return { task_id: taskId, task_key: task.key, task_title: task.title, current: { start_date: task.start_date, due_date: task.due_date }, proposed: { start_date: start, due_date: due }, expected_updated_at: stamp, source, reasons: [] };
        };
        const changes = [...direct.map((change) => row(change.task_id, change.start_date, change.due_date, "DIRECT")), ...schedule.cascade.map((item) => row(item.task_id, item.due_date, item.due_date, "CASCADE"))];
        return { json: { changes, conflicts: [], consequences_count: schedule.cascade.length, can_apply: true } };
    }
    if (rest === "/calendar/scenarios/apply" && method === "POST") {
        const changes = (body() as { changes: { task_id: number }[] }).changes;
        return { json: { applied_count: changes.length, task_ids: changes.map((change) => change.task_id) } };
    }
    return null;
}
