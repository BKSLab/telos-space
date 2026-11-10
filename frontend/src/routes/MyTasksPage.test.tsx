// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { MyTask, Project, TaskParticipantRole, TaskStatus } from "@/lib/types";
import type { TaskAttentionSnapshot } from "@/lib/taskAttention";
import { TaskAttentionContext } from "@/lib/useTaskAttention";
import { useUiStore } from "@/stores/ui";
import { MyTasksPage } from "./MyTasksPage";

const stamp = "2026-11-10T07:00:00Z";
const projects: Project[] = [
    { id: 1, key: "FIRST", name: "Первый проект", status: "ACTIVE", color: "#58a6ff", icon: null, description_md: null, start_date: null, due_date: null, order_index: 0, created_at: stamp, updated_at: stamp },
    { id: 2, key: "SECOND", name: "Второй проект", status: "ACTIVE", color: "#58a6ff", icon: null, description_md: null, start_date: null, due_date: null, order_index: 1, created_at: stamp, updated_at: stamp },
    { id: 3, key: "EMPTY", name: "Проект без моих задач", status: "PLANNING", color: "#58a6ff", icon: null, description_md: null, start_date: null, due_date: null, order_index: 2, created_at: stamp, updated_at: stamp },
];

function task(id: number, projectId: number | null, title: string, role: TaskParticipantRole = "EXECUTOR", state: TaskStatus = "IN_PROGRESS"): MyTask {
    const project = projects.find(item => item.id === projectId);
    return {
        revision: 1, id, project_id: projectId, stage_id: projectId, number: id,
        key: `${project?.key ?? "T"}-${id}`, title, description_md: null,
        priority: "MEDIUM", role: null, assignee: null, participants: [],
        start_date: null, due_date: null, baseline_start_date: null, baseline_due_date: null,
        completed_at: null, wbs_node_id: null, position: id * 1000, created_at: stamp, updated_at: stamp,
        comments_count: 0, last_comment: null, search_match_source: null, search_title: null, search_excerpt: null,
        context: project ? { kind: "PROJECT", id: project.id, key: project.key, name: project.name, color: project.color } : null,
        state, state_label: state === "DONE" ? "Готово" : "В работе", my_roles: [role],
    };
}

const clients: QueryClient[] = [];
function setup(tasks: MyTask[], updatedIds: number[] = [], projectsError?: Error) {
    const requests: URL[] = [];
    const snapshot: TaskAttentionSnapshot = {
        new_tasks: 0, overdue_tasks: 0, updated_tasks: updatedIds.length, total: updatedIds.length,
        items: updatedIds.map(task_id => ({ task_id, revision: 1, deadline_revision: 1, overdue: false, is_new: false, is_overdue: false, is_updated: true })),
    };
    let failProjects = Boolean(projectsError);
    const get = vi.spyOn(api, "get").mockImplementation(async <T,>(path: string): Promise<T> => {
        if (path === endpoints.projects()) {
            if (failProjects) { failProjects = false; throw projectsError; }
            return projects as T;
        }
        const url = new URL(path, "http://test");
        requests.push(url);
        const query = url.searchParams;
        const role = ({ doing: "EXECUTOR", assigned: "REPORTER", watching: "OBSERVER" } as const)[query.get("role") as "doing" | "assigned" | "watching"];
        return tasks.filter(item =>
            (!query.has("project_id") || item.project_id === Number(query.get("project_id"))) &&
            (!role || item.my_roles.includes(role)) &&
            (query.get("include_closed") === "true" || !["DONE", "CANCELLED"].includes(item.state)) &&
            (!query.has("attention") || updatedIds.includes(item.id)),
        ) as T;
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity, gcTime: 0 } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><MemoryRouter><TaskAttentionContext.Provider value={{ data: snapshot, error: null, status: "connected" }}><MyTasksPage /></TaskAttentionContext.Provider></MemoryRouter></QueryClientProvider>);
    return { requests, get, client };
}

const projectFilter = () => screen.getByRole("button", { name: /Фильтр по проекту$/ });
async function chooseProject(id?: number) {
    if (!screen.queryByRole("listbox")) await userEvent.click(projectFilter());
    const project = projects.find(item => item.id === id);
    await userEvent.click(await screen.findByRole("option", { name: project ? `${project.key} · ${project.name}` : "Все проекты" }));
}

beforeEach(() => useUiStore.setState({ selectedTaskId: null }));
afterEach(() => {
    cleanup();
    clients.splice(0).forEach(client => client.clear());
    vi.restoreAllMocks();
});

describe("фильтр проекта в моих задачах", () => {
    it("использует все доступные проекты и возвращает разовые задачи после сброса", async () => {
        const { client, requests } = setup([task(1, 1, "Задача первого проекта"), task(2, 2, "Задача второго проекта"), task(3, null, "Разовая задача")]);
        await screen.findByRole("button", { name: "Разовая задача" });
        const select = projectFilter();
        await userEvent.click(select);
        expect(within(await screen.findByRole("listbox")).getAllByRole("option").map(option => option.getAttribute("aria-label"))).toEqual([
            "Все проекты", "FIRST · Первый проект", "SECOND · Второй проект", "EMPTY · Проект без моих задач",
        ]);
        await chooseProject(1);
        await waitFor(() => expect(screen.queryByRole("button", { name: "Разовая задача" })).toBeNull());
        expect(screen.getByRole("button", { name: "Задача первого проекта" })).toBeTruthy();
        expect(screen.queryByRole("button", { name: "Задача второго проекта" })).toBeNull();
        await chooseProject(2);
        await screen.findByRole("button", { name: "Задача второго проекта" });
        expect(screen.queryByRole("button", { name: "Задача первого проекта" })).toBeNull();
        await act(async () => { await client.invalidateQueries({ queryKey: queryKeys.myTasks }); });
        expect(select.textContent).toContain("Второй проект");
        expect(requests.at(-1)?.searchParams.get("project_id")).toBe("2");
        expect(screen.getByRole("button", { name: "Задача второго проекта" })).toBeTruthy();
        expect(screen.queryByRole("button", { name: "Задача первого проекта" })).toBeNull();
        await chooseProject(3);
        await screen.findByText("Задач пока нет");
        await userEvent.click(screen.getByRole("button", { name: "Сбросить фильтр проекта" }));
        await screen.findByRole("button", { name: "Разовая задача" });
        expect(screen.getByRole("button", { name: "Задача первого проекта" })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Задача второго проекта" })).toBeTruthy();
    });

    it("сочетает проект с ролью, закрытыми задачами, событиями и поиском", async () => {
        const { requests } = setup([
            task(1, 1, "Открытая задача"), task(2, 1, "Порученная задача", "REPORTER"),
            task(3, 1, "План проверен", "EXECUTOR", "DONE"), task(4, 2, "План другого проекта"),
            task(5, null, "Личный план"),
        ], [3, 4]);
        await screen.findByRole("button", { name: "Личный план" });
        await chooseProject(1);
        await userEvent.click(screen.getByRole("button", { name: "Делаю" }));
        await userEvent.click(screen.getByRole("checkbox", { name: "Показывать закрытые" }));
        await userEvent.click(screen.getByRole("button", { name: "Комментарии и изменения · 2" }));
        await screen.findByRole("button", { name: "План проверен" });
        expect(screen.queryByRole("button", { name: "План другого проекта" })).toBeNull();
        expect(screen.queryByRole("button", { name: "Порученная задача" })).toBeNull();
        expect(requests.at(-1)?.searchParams.toString()).toBe("role=doing&include_closed=true&attention=updated&project_id=1");
        const search = screen.getByRole("textbox", { name: "Поиск по моим задачам" });
        await userEvent.type(search, "другого");
        expect(screen.getByText("Задач пока нет")).toBeTruthy();
        await userEvent.clear(search);
        expect(screen.getByRole("button", { name: "План проверен" })).toBeTruthy();
        await chooseProject();
        await screen.findByRole("button", { name: "План другого проекта" });
        expect(screen.getByRole("button", { name: "План проверен" })).toBeTruthy();
        expect(screen.queryByRole("button", { name: "Личный план" })).toBeNull();
    });

    it("сбрасывает страницу при выборе проекта и при возврате к общему списку", async () => {
        setup([...Array.from({ length: 60 }, (_, index) => task(index + 1, 1, `Задача ${index + 1}`)), task(61, 2, "Второй проект"), task(62, null, "Разовая задача")]);
        await screen.findByRole("button", { name: "Задача 1" });
        await userEvent.click(screen.getByRole("button", { name: "Дальше" }));
        expect(screen.getByText("2 / 2")).toBeTruthy();
        await chooseProject(1);
        await screen.findByRole("button", { name: "Задача 1" });
        expect(screen.getByText("1 / 2")).toBeTruthy();
        screen.getByRole("button", { name: "Дальше" }).focus();
        await userEvent.keyboard("{Enter}");
        expect(screen.getByRole("button", { name: "Задача 51" })).toBeTruthy();
        await chooseProject();
        await screen.findByRole("button", { name: "Задача 1" });
        expect(screen.getByText("1 / 2")).toBeTruthy();
    });

    it("сохраняет задачи при ошибке загрузки проектов и позволяет повторить запрос", async () => {
        setup([task(1, 1, "Доступная задача")], [], new Error("Список проектов недоступен"));
        await screen.findByRole("button", { name: "Доступная задача" });
        expect(await screen.findByRole("alert")).toBeTruthy();
        expect((projectFilter() as HTMLButtonElement).disabled).toBe(true);
        await userEvent.click(screen.getByRole("button", { name: "Повторить загрузку проектов" }));
        await waitFor(() => expect((projectFilter() as HTMLButtonElement).disabled).toBe(false));
        expect(screen.queryByRole("alert")).toBeNull();
        await userEvent.click(projectFilter());
        expect(await screen.findByRole("option", { name: "FIRST · Первый проект" })).toBeTruthy();
    });

    it("выбирает проект с клавиатуры и возвращает фокус после Escape и сброса", async () => {
        setup([task(1, 1, "Проектная задача"), task(2, null, "Разовая задача")]);
        await screen.findByRole("button", { name: "Разовая задача" });
        const trigger = projectFilter();
        trigger.focus();
        await userEvent.keyboard("{Enter}");
        await screen.findByRole("listbox");
        await userEvent.keyboard("{ArrowDown}{Enter}");
        await waitFor(() => expect(screen.queryByRole("button", { name: "Разовая задача" })).toBeNull());
        expect(projectFilter().textContent).toContain("Первый проект");
        await waitFor(() => expect(document.activeElement).toBe(trigger));
        await userEvent.keyboard("{Enter}");
        await screen.findByRole("listbox");
        await userEvent.keyboard("{Escape}");
        await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
        await waitFor(() => expect(document.activeElement).toBe(trigger));
        screen.getByRole("button", { name: "Сбросить фильтр проекта" }).focus();
        await userEvent.keyboard("{Enter}");
        await screen.findByRole("button", { name: "Разовая задача" });
        await waitFor(() => expect(document.activeElement).toBe(trigger));
        expect(screen.queryByRole("button", { name: "Сбросить фильтр проекта" })).toBeNull();
    });
});
