// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { queryKeys } from "@/lib/api";
import type { Task, TaskUpdate, UserSummary } from "@/lib/types";
import { useUiStore } from "@/stores/ui";
import { TaskDialog } from "./TaskDialog";

vi.mock("@/lib/useCalendarView", () => ({ useMediaQuery: () => true }));
vi.mock("@/lib/useTaskViewed", () => ({ useTaskViewed: () => {} }));

HTMLElement.prototype.scrollIntoView = vi.fn();

const stamp = "2026-11-07T10:00:00Z";
const owner: UserSummary = { id: 1, username: "owner", last_name: "Владелец", first_name: "Иван", middle_name: null, has_avatar: false };
const executor: UserSummary = { ...owner, id: 2, username: "executor", last_name: "Исполнитель" };
const task: Task = {
    id: 5, revision: 1, kind: "PROJECT", project_id: 1, stage_id: 1, number: 5, key: "DEMO-5",
    title: "Исходное название", description_md: "Исходное описание", priority: "MEDIUM", role: null, assignee: null,
    participants: [{ id: 1, role: "REPORTER", user: owner }], start_date: "2026-11-07", due_date: "2026-11-20",
    baseline_start_date: null, baseline_due_date: null, completed_at: null, position: 0, created_at: stamp, updated_at: stamp,
    comments_count: 0, last_comment: null, search_match_source: null, search_title: null, search_excerpt: null, wbs_node_id: null,
};
const clients: QueryClient[] = [];

function setup(initial = task) {
    const state = { task: { ...initial }, calls: [] as { method: string; path: string; body?: TaskUpdate }[] };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
        const path = new URL(String(input)).pathname;
        const method = init?.method ?? "GET";
        const body = init?.body ? JSON.parse(String(init.body)) as TaskUpdate : undefined;
        state.calls.push({ method, path, body });
        const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
        if (path === "/api/v1/tasks/5") {
            if (method === "PATCH") {
                if (body?.expected_revision !== undefined && body.expected_revision !== state.task.revision) {
                    return json({ detail: "Task revision conflict" }, 409);
                }
                const changes = { ...body };
                delete changes.expected_revision;
                state.task = { ...state.task, ...changes, revision: (state.task.revision ?? 0) + 1 };
            }
            return json(state.task);
        }
        if (path === "/api/v1/auth/me") return json({ ...owner, email: null, phone: null, telegram: null, created_at: stamp });
        if (path === "/api/v1/tasks/5/attention") return json({ revision: 9, deadline_revision: 0, overdue: false });
        if (path === "/api/v1/projects/1/stages") return json([{ id: 1, project_id: 1, name: "Новая", position: 0, color: "#777777", is_done_stage: false }]);
        if (path === "/api/v1/projects/1/members") return json([
            { id: 1, project_id: 1, role: "OWNER", user: owner, created_at: stamp },
            { id: 2, project_id: 1, role: "MEMBER", user: executor, created_at: stamp },
        ]);
        if (path === "/api/v1/projects/1/risks") return json({ total: 0, items: [] });
        if (path === "/api/v1/documents") return json({ items: [], total: 0, limit: 20, offset: 0, next_offset: null });
        if (["/api/v1/tasks/5/comments", "/api/v1/tasks/5/activity"].includes(path)) return json([]);
        throw new Error(`Unexpected request: ${method} ${path}`);
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } });
    clients.push(client);
    useUiStore.getState().setSelectedTaskId(5);
    render(<QueryClientProvider client={client}><MemoryRouter><TaskDialog /></MemoryRouter></QueryClientProvider>);
    const patches = () => state.calls.filter(call => call.method === "PATCH");
    const updateCache = async (changes: Partial<Task>) => {
        state.task = { ...state.task, ...changes };
        await act(async () => { client.setQueryData(queryKeys.task(5), state.task); });
        await waitFor(() => expect(screen.getByRole("heading", { name: state.task.title })).toBeTruthy());
    };
    return { state, client, patches, updateCache, user: userEvent.setup() };
}

afterEach(() => {
    cleanup();
    clients.splice(0).forEach(client => client.clear());
    useUiStore.getState().setSelectedTaskId(null);
    vi.restoreAllMocks();
});

it("сохраняет описание с версией открытия, удерживает черновик при 409 и принимает новую версию после повторного открытия", async () => {
    const { state, client, patches, updateCache, user } = setup();
    await user.click(await screen.findByRole("button", { name: "Редактировать описание" }));
    const field = screen.getByRole("textbox", { name: "Описание задачи в Markdown" });
    fireEvent.change(field, { target: { value: "Мой черновик" } });
    await updateCache({ revision: 3, description_md: "Чужое описание" });
    await user.click(screen.getByRole("button", { name: "Сохранить" }));
    await screen.findAllByText(/Задача уже изменена/);
    await waitFor(() => expect(client.getQueryData<Task>(queryKeys.task(5))?.revision).toBe(3));
    expect(patches().map(call => call.body)).toEqual([{ description_md: "Мой черновик", expected_revision: 1 }]);
    expect((field as HTMLTextAreaElement).value).toBe("Мой черновик");
    expect(state.task.description_md).toBe("Чужое описание");
    // Повторное нажатие не подменяет исходную версию свежей из кэша.
    await user.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(patches()).toHaveLength(2));
    expect(patches()[1].body?.expected_revision).toBe(1);
    await user.click(screen.getByRole("button", { name: "Отмена" }));
    await user.click(screen.getByRole("button", { name: "Редактировать описание" }));
    const reopened = screen.getByRole("textbox", { name: "Описание задачи в Markdown" });
    expect((reopened as HTMLTextAreaElement).value).toBe("Чужое описание");
    fireEvent.change(reopened, { target: { value: "Согласованное описание" } });
    await user.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(screen.queryByRole("textbox", { name: "Описание задачи в Markdown" })).toBeNull());
    expect(patches()[2].body).toEqual({ description_md: "Согласованное описание", expected_revision: 3 });
    expect(state.task.revision).toBe(4);
}, 15_000);

it("переименование сохраняет исходную версию и введённый текст при фоновом обновлении и конфликте", async () => {
    const { state, client, patches, user } = setup();
    await user.click(await screen.findByRole("button", { name: "Переименовать задачу" }));
    const field = screen.getByRole("textbox", { name: "Название задачи" });
    fireEvent.change(field, { target: { value: "Моё название" } });
    state.task = { ...state.task, revision: 3, title: "Чужое название" };
    // Настоящее фоновое перечитывание того же query не открывает новый черновик.
    await act(async () => { await client.invalidateQueries({ queryKey: queryKeys.task(5) }); });
    await user.click(screen.getByRole("button", { name: "Сохранить" }));
    await screen.findAllByText(/Задача уже изменена/);
    expect(patches()[0].body).toEqual({ title: "Моё название", expected_revision: 1 });
    expect((field as HTMLInputElement).value).toBe("Моё название");
    expect(state.task.title).toBe("Чужое название");
    await user.click(screen.getByRole("button", { name: "Отмена" }));
    await user.click(screen.getByRole("button", { name: "Переименовать задачу" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Название задачи" }), { target: { value: "Новое название" } });
    await user.click(screen.getByRole("button", { name: "Сохранить" }));
    await screen.findByRole("heading", { name: "Новое название" });
    expect(patches()[1].body).toEqual({ title: "Новое название", expected_revision: 3 });
}, 15_000);

it.each([
    { label: "Начало", field: "start_date", original: "2026-11-07", remote: "2026-11-09", draft: "2026-11-10" },
    { label: "Завершение", field: "due_date", original: "2026-11-20", remote: "2026-11-22", draft: "2026-11-25" },
] as const)("$label: фиксирует версию при фокусе, сохраняет выбранную дату при 409 и перечитывает после открытия карточки", async ({ label, field, original, remote, draft }) => {
    const { state, patches, updateCache, user } = setup();
    await screen.findByRole("heading", { name: task.title });
    const input = screen.getByLabelText(label, { exact: true }) as HTMLInputElement;
    fireEvent.focus(input);
    await updateCache({ revision: 3, [field]: remote });
    expect(input.value).toBe(original);
    fireEvent.change(input, { target: { value: draft } });
    await screen.findByText(/Задача уже изменена/);
    expect(patches()[0].body).toEqual({ [field]: draft, expected_revision: 1 });
    expect(input.value).toBe(draft);
    expect(state.task[field]).toBe(remote);
    await user.click(screen.getByRole("button", { name: "Закрыть задачу" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await act(async () => { useUiStore.getState().setSelectedTaskId(5); });
    const dialog = await screen.findByRole("dialog", { name: "Задача DEMO-5" });
    const reopened = within(dialog).getByLabelText(label, { exact: true }) as HTMLInputElement;
    expect(reopened.value).toBe(remote);
    fireEvent.focus(reopened);
    fireEvent.change(reopened, { target: { value: draft } });
    await waitFor(() => expect(state.task[field]).toBe(draft));
    expect(patches()[1].body).toEqual({ [field]: draft, expected_revision: 3 });
}, 15_000);

it("непосредственный выбор приоритета использует актуальную версию и не закрывает открытый черновик описания", async () => {
    const { patches, updateCache, user } = setup();
    await user.click(await screen.findByRole("button", { name: "Редактировать описание" }));
    const field = screen.getByRole("textbox", { name: "Описание задачи в Markdown" });
    fireEvent.change(field, { target: { value: "Несохранённое описание" } });
    await updateCache({ revision: 3 });
    fireEvent.change(screen.getByRole("combobox", { name: "Приоритет" }), { target: { value: "HIGH" } });
    await waitFor(() => expect(patches()[0].body).toEqual({ priority: "HIGH", expected_revision: 3 }));
    await waitFor(() => expect((screen.getByRole("combobox", { name: "Приоритет" }) as HTMLSelectElement).disabled).toBe(false));
    expect((screen.getByRole("textbox", { name: "Описание задачи в Markdown" }) as HTMLTextAreaElement).value).toBe("Несохранённое описание");
    await user.click(screen.getByRole("button", { name: "Сохранить" }));
    await screen.findByText(/Задача уже изменена/);
    expect(patches()[1].body).toEqual({ description_md: "Несохранённое описание", expected_revision: 1 });
}, 15_000);

it("выбор участника использует актуальную версию карточки", async () => {
    const { patches, updateCache, user } = setup();
    await screen.findByRole("heading", { name: task.title });
    await updateCache({ revision: 3 });
    await user.click(screen.getByRole("tab", { name: "Участники" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Исполнитель" }), { target: { value: "2" } });
    await waitFor(() => expect(patches()[0].body).toEqual({ executor_id: 2, expected_revision: 3 }));
}, 15_000);
