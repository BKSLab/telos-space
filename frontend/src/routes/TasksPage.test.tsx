// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { ProjectStage, Task } from "@/lib/types";
import { useUiStore } from "@/stores/ui";
import { TasksPage } from "./TasksPage";

vi.mock("@/lib/useProjectOutlet", () => ({ useProjectOutlet: () => ({ id: 1, key: "DEMO" }) }));
vi.mock("@/lib/useRisks", () => ({ useRiskTaskCounts: () => ({ data: {} }) }));
vi.mock("@/lib/toast", () => ({ useToast: () => ({ error: vi.fn() }) }));
vi.mock("@/components/tasks/CreateTaskDialog", () => ({ CreateTaskDialog: () => null }));

const stages: ProjectStage[] = [
    { id: 1, project_id: 1, name: "План", color: "#58a6ff", order_index: 0, is_done_stage: false },
    { id: 2, project_id: 1, name: "Готово", color: "#3fb950", order_index: 1, is_done_stage: true },
];
function task(id: number, stageId: number, priority: Task["priority"], position = 1000): Task {
    return { id, revision: 1, project_id: 1, stage_id: stageId, position, key: `DEMO-${id}`, number: id, title: `Подготовка ${id}`,
        description_md: null, priority, role: null, assignee: null, participants: [], start_date: null, due_date: null,
        baseline_start_date: null, baseline_due_date: null, completed_at: null, created_at: "2026-11-16", updated_at: "2026-11-16",
        comments_count: 0, last_comment: null, search_match_source: null, search_title: null, search_excerpt: null, wbs_node_id: null };
}
const tasks = [task(10, 1, "HIGH"), task(20, 2, "HIGH", 2000), task(30, 2, "LOW", 9000)];
const clients: QueryClient[] = [];

function Location() {
    const location = useLocation();
    const navigate = useNavigate();
    return <><output aria-label="Адрес">{location.pathname}{location.search}{location.hash}</output><button onClick={() => navigate(-1)}>Назад</button><button onClick={() => navigate(1)}>Вперёд</button></>;
}
function setup(path = "/projects/DEMO/tasks") {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
    clients.push(client);
    client.setQueryData(queryKeys.stages(1), stages);
    client.setQueryData(queryKeys.tasks(1, ""), tasks);
    client.setQueryData(queryKeys.tasks(1, "Подготовка"), tasks);
    const get = vi.spyOn(api, "get").mockImplementation(async path => path === endpoints.projectStages(1) ? stages : tasks);
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><TasksPage /><Location /></MemoryRouter></QueryClientProvider>);
    return { client, get };
}

afterEach(() => {
    cleanup();
    clients.splice(0).forEach(client => client.clear());
    useUiStore.setState({ selectedTaskId: null });
    vi.restoreAllMocks();
});

it("по умолчанию открывает канбан, а явный список открывает ту же задачу", () => {
    setup();
    const views = within(screen.getByRole("navigation", { name: "Представление задач" }));
    expect(views.getByRole("link", { name: "Канбан" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("region", { name: "Колонка План" })).toBeTruthy();
    fireEvent.click(views.getByRole("link", { name: "Список" }));
    expect(screen.getByRole("table")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Колонка План" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "DEMO-10" }));
    expect(useUiStore.getState().selectedTaskId).toBe(10);
});

it("сохраняет фильтры, поиск, task, confirm и фрагмент при переключении и навигации назад", () => {
    const { get } = setup("/projects/DEMO/tasks?view=list&search=Подготовка&stage=1&priority=HIGH&task=10&confirm=action-id#context");
    useUiStore.setState({ selectedTaskId: 10 });
    expect(screen.getAllByRole("row")).toHaveLength(2);
    fireEvent.click(screen.getByRole("link", { name: "Канбан" }));
    expect((screen.getByLabelText("Поиск задач проекта") as HTMLInputElement).value).toBe("Подготовка");
    expect((screen.getByLabelText("Фильтр по стадии") as HTMLSelectElement).value).toBe("1");
    expect((screen.getByLabelText("Фильтр по приоритету") as HTMLSelectElement).value).toBe("HIGH");
    const address = screen.getByLabelText("Адрес").textContent!;
    expect(address).toContain("task=10");
    expect(address).toContain("confirm=action-id");
    expect(address).toContain("#context");
    expect(address).not.toContain("view=list");
    expect(screen.getAllByRole("heading", { level: 3 })).toHaveLength(1);
    expect(useUiStore.getState().selectedTaskId).toBe(10);
    fireEvent.click(screen.getByRole("button", { name: "Назад" }));
    expect(screen.getByRole("table")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Вперёд" }));
    expect(screen.getByRole("region", { name: "Колонка План" })).toBeTruthy();
    expect(get).not.toHaveBeenCalled();
});

it("при переносе учитывает скрытые приоритетом задачи целевой стадии", async () => {
    const patch = vi.spyOn(api, "patch").mockResolvedValue({ ...tasks[0], stage_id: 2, position: 10000 });
    setup("/projects/DEMO/tasks?priority=HIGH");
    expect(screen.queryByRole("heading", { name: "Подготовка 30" })).toBeNull();
    fireEvent.change(screen.getByLabelText("Стадия задачи DEMO-10"), { target: { value: "2" } });
    await waitFor(() => expect(patch).toHaveBeenCalledWith(endpoints.taskStagePlacement(10), { stage_id: 2, position: 10000 }));
});

it("открывает список по прямой ссылке и сохраняет изменённые фильтры в обоих видах", () => {
    setup("/projects/DEMO/tasks?view=list");
    expect(screen.getByRole("table")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Фильтр по стадии"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Фильтр по приоритету"), { target: { value: "LOW" } });
    expect(screen.getAllByRole("row")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "DEMO-30" })).toBeTruthy();
    fireEvent.click(screen.getByRole("link", { name: "Канбан" }));
    expect(screen.getAllByRole("heading", { level: 3 })).toHaveLength(1);
    expect(screen.getByRole("heading", { name: "Подготовка 30" })).toBeTruthy();
});

it("объясняет отсутствие совпадений при фильтрах и возвращает задачи после очистки", () => {
    setup("/projects/DEMO/tasks?stage=1&priority=LOW");
    expect(screen.getByText("Ничего не найдено")).toBeTruthy();
    expect(screen.getByText("Измените запрос или снимите фильтры.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Создать задачу" })).toBeNull();
    fireEvent.change(screen.getByLabelText("Фильтр по приоритету"), { target: { value: "" } });
    expect(screen.getByRole("heading", { name: "Подготовка 10" })).toBeTruthy();
});

it("неизвестный вид использует канбан", () => {
    setup("/projects/DEMO/tasks?view=unknown");
    expect(screen.getByRole("link", { name: "Канбан" }).getAttribute("aria-current")).toBe("page");
    expect(screen.queryByRole("table")).toBeNull();
});
