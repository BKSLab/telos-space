// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PortfolioPage } from "@/routes/PortfolioPage";
import { ProjectNavigation } from "@/components/layout/ProjectNavigation";
import { api, queryKeys } from "@/lib/api";
import type { Dashboard, DashboardProject, Project } from "@/lib/types";
import { useUiStore } from "@/stores/ui";
import type { ReactNode } from "react";

const project: Project = {
    id: 1, key: "DEMO", name: "Запуск продукта", status: "ACTIVE", color: "#58a6ff", icon: "🚀",
    description_md: null, start_date: null, due_date: null, order_index: 0,
    created_at: "2026-11-04T07:00:00Z", updated_at: "2026-11-04T07:00:00Z",
};
const card: DashboardProject = { ...project, total_tasks: 4, done_tasks: 1, in_progress_tasks: 2, overdue_tasks: 1, completion_rate: 0.25, next_due_date: "2999-01-01" };
const dashboard: Dashboard = {
    totals: { total_projects: 3, active_projects: 1, total_tasks: 4, done_tasks: 1, in_progress_tasks: 2, overdue_tasks: 1, completion_rate: 0.25 },
    projects: [card, { ...card, id: 2, key: "PLAN", name: "Подготовка", status: "PLANNING", total_tasks: 0, done_tasks: 0, completion_rate: 0 },
        { ...card, id: 3, key: "OLD", name: "Архивный проект", status: "ARCHIVED" }],
    attention_tasks: [], recent_tasks: [], has_outside_tasks: false,
};
const clients: QueryClient[] = [];
function HistoryControls() {
    const navigate = useNavigate();
    const location = useLocation();
    return <><button onClick={() => navigate(-1)}>Назад</button><output data-testid="location">{location.pathname}{location.search}</output></>;
}
function setup(node: ReactNode = <PortfolioPage />, path = "/?view=projects", data: Dashboard | null = dashboard) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    clients.push(client);
    if (data) client.setQueryData(queryKeys.dashboard, data);
    client.setQueryData(queryKeys.projects, dashboard.projects);
    client.setQueryData(queryKeys.myTaskList("all", false), []);
    client.setQueryData(queryKeys.dashboardAnalytics(null), null);
    const view = render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}>{node}</MemoryRouter></QueryClientProvider>);
    return { ...view, client };
}
beforeEach(() => useUiStore.setState({ portfolioView: "projects", projectListCollapsed: false, selectedTaskId: null }));
afterEach(() => {
    cleanup();
    clients.splice(0).forEach(client => client.clear());
    localStorage.clear();
    vi.restoreAllMocks();
});

describe("портфель", () => {
    it("показывает все статусы и фактический прогресс, а сводку открывает отдельной вкладкой", async () => {
        setup();
        const catalog = within(screen.getByRole("table", { name: "Проекты портфеля" }));
        expect(catalog.getAllByRole("row")).toHaveLength(4);
        expect(catalog.getByRole("progressbar", { name: "Прогресс проекта Запуск продукта" }).getAttribute("aria-valuenow")).toBe("25");
        expect(catalog.getByText("В архиве")).toBeTruthy();
        expect(catalog.getByText("Планирование")).toBeTruthy();
        expect(screen.queryByRole("region", { name: "Показатели портфеля" })).toBeNull();
        const navigation = within(screen.getByRole("navigation", { name: "Представления портфеля" }));
        await userEvent.click(navigation.getByRole("link", { name: "Сводка" }));
        expect(screen.getByRole("region", { name: "Показатели портфеля" })).toBeTruthy();
        expect(screen.queryByRole("region", { name: "Каталог проектов" })).toBeNull();
        expect(useUiStore.getState().portfolioView).toBe("summary");
    });

    it("совмещает поиск по ключу со статусом и сбрасывает фильтры из пустого результата", async () => {
        setup();
        await userEvent.type(screen.getByRole("searchbox", { name: "Поиск проектов" }), "  demo ");
        const table = () => within(screen.getByRole("table", { name: "Проекты портфеля" }));
        expect(table().getAllByRole("row")).toHaveLength(2);
        expect(table().getByRole("link", { name: /Запуск продукта/ }).getAttribute("href")).toBe("/projects/DEMO");
        await userEvent.selectOptions(screen.getByRole("combobox", { name: "Статус проекта" }), "ARCHIVED");
        expect(screen.getByText("Проекты не найдены")).toBeTruthy();
        await userEvent.click(screen.getByRole("button", { name: "Сбросить фильтры" }));
        expect(table().getAllByRole("row")).toHaveLength(4);
        expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe("");
    });

    it("восстанавливает вкладку при возвращении и учитывает явную ссылку", async () => {
        const view = setup(undefined, "/?view=summary");
        await waitFor(() => expect(useUiStore.getState().portfolioView).toBe("summary"));
        view.unmount();
        const restored = setup(undefined, "/");
        expect(screen.getByRole("region", { name: "Сводка портфеля" })).toBeTruthy();
        restored.unmount();
        setup(undefined, "/?view=projects");
        expect(screen.getByRole("region", { name: "Каталог проектов" })).toBeTruthy();
        await waitFor(() => expect(useUiStore.getState().portfolioView).toBe("projects"));
    });

    it("предлагает создание первого проекта", () => {
        setup(undefined, undefined, { ...dashboard, projects: [] });
        expect(screen.getByText("Проектов пока нет")).toBeTruthy();
        expect(screen.getByRole("link", { name: "Создать проект" }).getAttribute("href")).toBe("/projects/new");
        expect(screen.queryByRole("button", { name: "Сбросить фильтры" })).toBeNull();
    });

    it("возвращает прежнюю вкладку кнопкой Назад после входа по корневому адресу", async () => {
        useUiStore.setState({ portfolioView: "summary" });
        setup(<><HistoryControls /><PortfolioPage /></>, "/");
        await waitFor(() => expect(screen.getByTestId("location").textContent).toBe("/?view=summary"));
        await userEvent.click(within(screen.getByRole("navigation", { name: "Представления портфеля" })).getByRole("link", { name: "Проекты" }));
        await waitFor(() => expect(useUiStore.getState().portfolioView).toBe("projects"));
        await userEvent.click(screen.getByRole("button", { name: "Назад" }));
        expect(await screen.findByRole("region", { name: "Сводка портфеля" })).toBeTruthy();
        expect(screen.getByTestId("location").textContent).toBe("/?view=summary");
    });

    it("показывает сбой загрузки и позволяет повторить запрос", async () => {
        const get = vi.spyOn(api, "get").mockRejectedValueOnce(new Error("Сводка недоступна")).mockResolvedValue(dashboard);
        setup(undefined, undefined, null);
        expect(await screen.findByText("Сводка недоступна")).toBeTruthy();
        await userEvent.click(screen.getByRole("button", { name: "Повторить загрузку" }));
        expect(await screen.findByRole("table", { name: "Проекты портфеля" })).toBeTruthy();
        expect(get).toHaveBeenCalledTimes(2);
    });
});

describe("быстрый переход к проектам", () => {
    it("сворачивает только список, сохраняет выбор и выделяет текущий проект", async () => {
        const view = setup(<ProjectNavigation />, "/projects/DEMO");
        expect(screen.getByRole("link", { name: project.name }).getAttribute("aria-current")).toBe("page");
        await userEvent.click(screen.getByRole("button", { name: "Свернуть список проектов" }));
        expect(screen.queryByRole("link", { name: project.name })).toBeNull();
        expect(useUiStore.getState().sidebarCollapsed).toBe(false);
        view.unmount();
        setup(<ProjectNavigation mobile />, "/projects/DEMO");
        expect(screen.getByRole("button", { name: "Развернуть список проектов" }).getAttribute("aria-expanded")).toBe("false");
        await userEvent.click(screen.getByRole("button", { name: "Развернуть список проектов" }));
        expect(screen.getByRole("link", { name: project.name })).toBeTruthy();
        const stored = JSON.parse(localStorage.getItem("telos-workspace-preferences")!);
        expect(stored.state).toEqual({ projectListCollapsed: false, portfolioView: "projects" });
    });

    it("сохраняет доступные имена в узкой панели и закрывает мобильное меню при переходе", async () => {
        const onNavigate = vi.fn();
        setup(<ProjectNavigation compact onNavigate={onNavigate} />);
        await userEvent.click(screen.getByRole("link", { name: project.name }));
        expect(onNavigate).toHaveBeenCalledOnce();
        expect(screen.getByRole("link", { name: "Новый проект" })).toBeTruthy();
    });

    it("ищет среди длинного списка, сохраняя возможность создать проект", async () => {
        const { client, rerender } = setup(<ProjectNavigation />);
        client.setQueryData(queryKeys.projects, Array.from({ length: 10 }, (_, index) => ({ ...project, id: index + 1, key: `P${index}`, name: `Проект ${index}` })));
        const search = await screen.findByRole("textbox", { name: "Быстрый поиск проектов" });
        await userEvent.type(search, "p9");
        expect(screen.getByRole("link", { name: "Проект 9" })).toBeTruthy();
        expect(screen.queryByRole("link", { name: "Проект 1" })).toBeNull();
        expect(screen.getByRole("link", { name: "Новый проект" })).toBeTruthy();
        rerender(<QueryClientProvider client={client}><MemoryRouter><ProjectNavigation compact /></MemoryRouter></QueryClientProvider>);
        expect(screen.getByRole("link", { name: "Проект 1" })).toBeTruthy();
        expect(screen.getByRole("link", { name: "Проект 9" })).toBeTruthy();
    });
});
