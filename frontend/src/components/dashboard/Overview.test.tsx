// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { api, endpoints, queryKeys } from "@/lib/api";
import { formatDateOnly } from "@/lib/dates";
import type { AnalyticsReport, Dashboard, DashboardProject, MyTask, Project, ProjectStats, Task } from "@/lib/types";
import type { RiskSummary } from "@/lib/risks";
import { useUiStore } from "@/stores/ui";
import { DashboardPage } from "@/routes/DashboardPage";
import { ProjectPulsePage } from "@/routes/ProjectPulsePage";
import { ProjectCard } from "@/components/dashboard/ProjectCard";
import { TaskCompletion } from "@/components/dashboard/OverviewCards";
import { PulseBoard } from "@/components/pulse/PulseBoard";

vi.mock("@/lib/useProjectOutlet", () => ({ useProjectOutlet: () => project }));

const project: Project = {
    id: 7, key: "DEMO", name: "Тестовый проект", status: "ACTIVE", color: "#58a6ff", icon: null,
    description_md: "## Описание\n\n**Важный этап**", start_date: null, due_date: null,
    order_index: 0, created_at: "2026-01-01T10:00:00Z", updated_at: "2026-11-02T10:00:00Z",
};
const stats: ProjectStats = {
    project_id: 7, total_tasks: 4, done_tasks: 1, in_progress_tasks: 2, overdue_tasks: 1,
    due_soon_tasks: 0, unassigned_tasks: 1, completion_rate: 0.25, next_due_date: "2999-01-01",
    stage_breakdown: [
        { stage_id: 1, stage_name: "Новые", color: "#7d8793", is_done_stage: false, tasks_count: 1 },
        { stage_id: 2, stage_name: "В работе", color: "#58a6ff", is_done_stage: false, tasks_count: 2 },
        { stage_id: 3, stage_name: "Готово", color: "#3fb950", is_done_stage: true, tasks_count: 1 },
    ],
};
const card: DashboardProject = { ...project, ...stats };
const riskSummary: RiskSummary = {
    total_risks: 0, active_risks: 0, open_risks: 0, mitigating_risks: 0, occurred_risks: 0, closed_risks: 0,
    high_risks: 0, medium_risks: 0, low_risks: 0, risks_without_owner: 0, risks_without_mitigation: 0,
    risks_due_for_review: 0, risks_review_overdue: 0, risks_linked_to_tasks: 0, ai_suggested_risks: 0,
    latest_update: null, matrix: [], signals: [],
};
const historicalReport: AnalyticsReport = {
    id: 1, scope: "PROJECT", project_id: 7, project_key: "DEMO", project_name: project.name,
    created_at: "2020-01-01T10:00:00Z", created_by: "Автор", llm_model: "test", duration_ms: 100,
    headline: "Сохранённая оценка Ариса", health: "CRITICAL", health_note: "Нужна проверка зависимостей.",
    findings: [{ kind: "BLOCKER", severity: "HIGH", title: "Проверить зависимость", detail: "Историческое наблюдение.", project_key: "DEMO", project_name: project.name,
        tasks: [{ id: 12, key: "DEMO-12", title: "Проверить контракт", project_key: "DEMO", due_date: "2020-01-01", is_overdue: true }] }],
    progress: [], recommendations: [],
    signals: { total_tasks: 900, done_tasks: 0, overdue_tasks: 900, due_soon_tasks: 0, no_due_date_tasks: 0, unassigned_tasks: 0, stale_tasks: 0, blocked_tasks: 1, milestones_at_risk: 0, unplaced_tasks: 0 },
    context: { projects: 1, tasks_total: 900, tasks_included: 900, comments_included: 0, documents_included: 0, stickers_included: 0, milestones_included: 0, activity_included: 0, truncated: false, omitted: [], wbs_nodes_included: 0 },
};

function task(id: number, stage_id: number, due_date: string | null, title: string): Task {
    return { id, project_id: 7, stage_id, number: id, key: `DEMO-${id}`, title, due_date, description_md: null, priority: "LOW", role: null, assignee: null, participants: [],
        start_date: null, baseline_start_date: null, baseline_due_date: null, completed_at: null, position: id,
        created_at: "2026-01-01T10:00:00Z", updated_at: "2026-11-02T10:00:00Z", comments_count: 0,
        last_comment: null, search_match_source: null, search_title: null, search_excerpt: null, wbs_node_id: null };
}

const clients: QueryClient[] = [];
function client() {
    const value = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
    clients.push(value);
    return value;
}
function renderPage(node: ReactNode, queryClient: QueryClient) {
    return render(<QueryClientProvider client={queryClient}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>);
}
function seedProject(queryClient: QueryClient) {
    queryClient.setQueryData(queryKeys.projectStats(7), stats);
    queryClient.setQueryData(queryKeys.projectMembers(7), []);
    queryClient.setQueryData(queryKeys.projectRiskSummary(7, `today=${formatDateOnly(new Date())}`), riskSummary);
    queryClient.setQueryData(queryKeys.dashboardAnalytics(7), historicalReport);
}

afterEach(() => {
    cleanup();
    clients.splice(0).forEach(queryClient => queryClient.clear());
    useUiStore.setState({ selectedTaskId: null });
    vi.restoreAllMocks();
});

describe("обзоры", () => {
    it("показывает живые KPI портфеля независимо от старого разбора и отдельно открывает разовую задачу", async () => {
        const queryClient = client();
        const dashboard: Dashboard = { totals: { total_projects: 2, active_projects: 1, total_tasks: 12, done_tasks: 3, in_progress_tasks: 4, overdue_tasks: 2, completion_rate: 0.25 },
            projects: [card, { ...card, id: 8, key: "PAUSED", name: "На паузе", status: "PAUSED", total_tasks: 8, done_tasks: 2 }],
            attention_tasks: [{ id: 12, key: "DEMO-12", title: "Проверить контракт", project_id: 7, project_key: "DEMO", project_name: project.name, project_color: project.color, stage_id: 2, stage_name: "В работе", priority: "LOW", due_date: "2020-01-01", is_overdue: true, updated_at: project.updated_at }], recent_tasks: [], has_outside_tasks: true };
        const personal: MyTask = { ...task(51, 1, null, "Купить материалы"), project_id: null, stage_id: null, context: null, state: "NEW", state_label: "Новая", my_roles: ["EXECUTOR"] };
        queryClient.setQueryData(queryKeys.dashboard, dashboard);
        queryClient.setQueryData(queryKeys.myTaskList("all", false), [personal]);
        queryClient.setQueryData(queryKeys.dashboardAnalytics(null), { ...historicalReport, scope: "PORTFOLIO", project_id: null });
        renderPage(<DashboardPage />, queryClient);

        const metrics = within(screen.getByRole("region", { name: "Показатели портфеля" }));
        expect(metrics.getByText("25%")).toBeTruthy();
        expect(metrics.queryByText("900")).toBeNull();
        expect(screen.getByText(/Показатели по всем доступным проектам/)).toBeTruthy();
        expect(screen.getByRole("img", { name: "Выполнено 3 из 12 задач: 25%" })).toBeTruthy();
        await userEvent.click(screen.getByRole("button", { name: /Проверить контракт/ }));
        expect(useUiStore.getState().selectedTaskId).toBe(12);
        await userEvent.click(screen.getByRole("button", { name: /Купить материалы/ }));
        expect(useUiStore.getState().selectedTaskId).toBe(51);

        act(() => queryClient.setQueryData(queryKeys.dashboard, { ...dashboard, totals: { ...dashboard.totals, done_tasks: 6, completion_rate: 0.5 } }));
        await waitFor(() => expect(metrics.getByText("50%")).toBeTruthy());
        expect(screen.getByText("Сохранённая оценка Ариса")).toBeTruthy();
    });

    it("в проекте исключает сроки завершённых задач, сохраняет стадии и действие открытия карточки", async () => {
        const queryClient = client();
        seedProject(queryClient);
        queryClient.setQueryData(queryKeys.tasks(7), [task(11, 1, null, "Уточнить требования"), task(12, 2, "2020-01-01", "Открытая просроченная"), task(13, 3, "2019-01-01", "Завершённая со старым сроком"), task(14, 2, "2999-01-01", "Будущая задача")]);
        renderPage(<ProjectPulsePage />, queryClient);
        expect(screen.queryByRole("button", { name: /Завершённая со старым сроком/ })).toBeNull();
        expect(screen.getByRole("progressbar", { name: "Доля задач в стадии «В работе»" }).getAttribute("aria-valuenow")).toBe("50");
        expect(screen.getByRole("img", { name: "Выполнено 1 из 4 задач: 25%" })).toBeTruthy();
        expect(screen.getByRole("link", { name: /Размещение в ИСР/ }).getAttribute("href")).toBe("/projects/DEMO/structure");
        await userEvent.click(screen.getByRole("button", { name: /Открытая просроченная/ }));
        expect(useUiStore.getState().selectedTaskId).toBe(12);
    });

    it("при сбое списка задач сохраняет показатели и предлагает повторить загрузку", async () => {
        const queryClient = client();
        seedProject(queryClient);
        vi.spyOn(api, "get").mockRejectedValue(new Error("Список задач недоступен"));
        renderPage(<ProjectPulsePage />, queryClient);
        expect(await screen.findByText("Задачи недоступны")).toBeTruthy();
        expect(screen.getByRole("region", { name: "Показатели проекта" })).toBeTruthy();
        expect(screen.getByRole("button", { name: "Повторить загрузку" })).toBeTruthy();
    });
});

it("превью проекта использует обработку Markdown и не создаёт вложенные ссылки", async () => {
    render(<MemoryRouter><ProjectCard project={{ ...card, description_md: "## Описание\n\n**Важный** [этап](https://example.com)" }} /></MemoryRouter>);
    expect(await screen.findByText("Описание Важный этап")).toBeTruthy();
    expect(screen.queryByText(/##/)).toBeNull();
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("25");
});

it("без задач не изображает выполненную работу", () => {
    render(<TaskCompletion total={0} done={0} inProgress={0} />);
    expect(screen.getByRole("img", { name: "Задач пока нет" })).toBeTruthy();
    expect(screen.getByText("—")).toBeTruthy();
    expect(screen.queryByText("100%")).toBeNull();
});

it("показывает дату старого вывода, раскрывает исторические сигналы и обновляет разбор по кнопке", async () => {
    const queryClient = client();
    queryClient.setQueryData(queryKeys.dashboardAnalytics(7), historicalReport);
    const onOpenTask = vi.fn();
    const post = vi.spyOn(api, "post").mockResolvedValue({ ...historicalReport, id: 2, headline: "Обновлённая оценка", created_at: "2999-01-01T10:00:00Z" });
    renderPage(<PulseBoard projectId={7} dataUpdatedAt={project.updated_at} onOpenTask={onOpenTask} />, queryClient);
    expect(screen.getByText(/Данные менялись после разбора/)).toBeTruthy();
    const summary = screen.getByText(/Полный разбор ·/);
    const details = summary.closest("details")!;
    expect(details.hasAttribute("open")).toBe(false);
    expect(within(details).getByText("Сигналы на момент разбора")).toBeTruthy();
    expect(within(details).getByText("900")).toBeTruthy();
    await userEvent.click(summary);
    expect(details.hasAttribute("open")).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "DEMO-12" }));
    expect(onOpenTask).toHaveBeenCalledWith(12);
    await userEvent.click(screen.getByRole("button", { name: "Обновить разбор" }));
    await waitFor(() => expect(screen.getByText("Обновлённая оценка")).toBeTruthy());
    expect(post).toHaveBeenCalledWith(endpoints.dashboardAnalytics(), { project_id: 7 });
    expect(screen.getByText(/Полный разбор ·/).closest("details")!.hasAttribute("open")).toBe(false);
});
