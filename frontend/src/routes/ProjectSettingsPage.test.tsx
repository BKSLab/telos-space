// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Link, MemoryRouter, Outlet, Route, Routes, useParams } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { api, ApiError, endpoints } from "@/lib/api";
import type { Project, ProjectUpdate } from "@/lib/types";
import { ProjectSettingsPage } from "./ProjectSettingsPage";

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("@/lib/toast", () => ({ useToast: () => toast }));
vi.mock("@/lib/useAuth", () => ({ useCurrentUser: () => ({ data: { id: 1 } }) }));
vi.mock("@/components/projects/ProjectPeopleSummary", () => ({ ProjectPeopleSummary: () => null }));
vi.mock("@/components/projects/ProjectDeadlineHistory", () => ({ ProjectDeadlineHistory: () => null }));
vi.mock("@/components/projects/ProjectContextHistory", () => ({ ProjectContextHistory: () => null }));
vi.mock("@/components/projects/ProjectGitSources", () => ({ ProjectGitSources: () => null }));
vi.mock("@/routes/ProjectTeamPage", () => ({ ProjectTeamPage: () => null }));

const projectA: Project = {
    id: 1, key: "ALPHA", name: "Альфа", owner_id: 1, context_revision: 2,
    description_sections: { problem: "Проблема Альфы", goal: "Цель Альфы", expected_result: "", additional: "Заметки Альфы" },
    description_md: "Цель Альфы", status: "ACTIVE", color: "#58a6ff", icon: null,
    start_date: null, due_date: null, order_index: 0,
    created_at: "2026-11-01T00:00:00Z", updated_at: "2026-11-16T00:00:00Z",
};
const projectB: Project = {
    ...projectA, id: 2, key: "BETA", name: "Бета", description_md: "Цель Беты",
    description_sections: { problem: "Проблема Беты", goal: "Цель Беты", expected_result: "Результат Беты", additional: "Заметки Беты" },
};
const projects = [projectA, projectB];
const clients: QueryClient[] = [];

function ProjectRoute() {
    const { projectKey } = useParams();
    const project = projects.find(item => item.key === projectKey);
    return <>
        <Link to="/projects/ALPHA/settings">К Альфе</Link>
        <Link to="/projects/BETA/settings">К Бете</Link>
        <Outlet context={{ project }} />
    </>;
}

function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (reason: unknown) => void;
    const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
    return { promise, resolve, reject };
}

function setup() {
    const get = vi.spyOn(api, "get").mockImplementation(async path => {
        if (path.endsWith("/stages")) return [];
        throw new Error(`Неожиданное чтение в UI-тесте: ${path}`);
    });
    const patch = vi.spyOn(api, "patch").mockImplementation(async (path, body) => {
        const project = projects.find(item => path === endpoints.project(item.id));
        if (!project) throw new Error(`Неожиданная запись в UI-тесте: ${path}`);
        const changes = body as ProjectUpdate;
        return { ...project, ...changes,
            description_sections: changes.description_sections === null ? null : { ...project.description_sections, ...changes.description_sections },
            context_revision: (changes.expected_context_revision ?? project.context_revision ?? 0) + 1 };
    });
    const client = new QueryClient({ defaultOptions: {
        queries: { retry: false, gcTime: Infinity }, mutations: { retry: false, gcTime: Infinity },
    } });
    clients.push(client);
    render(<QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/projects/ALPHA/settings"]}>
            <Routes><Route path="/projects/:projectKey" element={<ProjectRoute />}>
                <Route path="settings" element={<ProjectSettingsPage />} />
            </Route></Routes>
        </MemoryRouter>
    </QueryClientProvider>);
    openPassport();
    return { client, get, patch };
}

function openPassport() {
    fireEvent.click(screen.getByText("Редактировать паспорт", { exact: true }));
}

function field(name: string) {
    return screen.getByLabelText(name) as HTMLInputElement | HTMLTextAreaElement;
}

function changeGoal(value: string) {
    fireEvent.change(field("Цель"), { target: { value } });
}

function save() {
    fireEvent.click(screen.getByRole("button", { name: /^Сохранить$/ }));
}

afterEach(() => {
    cleanup();
    clients.splice(0).forEach(client => client.clear());
    vi.restoreAllMocks();
    toast.success.mockClear();
    toast.error.mockClear();
});

it("при переходе между настройками проектов отделяет черновик и ревизию от адреса PATCH", async () => {
    const { patch } = setup();
    changeGoal("Черновик Альфы");
    fireEvent.click(screen.getByRole("link", { name: "К Бете" }));
    openPassport();
    expect(field("Название").value).toBe("Бета");
    expect(field("Цель").value).toBe("Цель Беты");
    expect((screen.getByRole("button", { name: /^Сохранить$/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(patch).not.toHaveBeenCalled();
    changeGoal("Моя цель Беты");
    save();
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(patch).toHaveBeenCalledWith(endpoints.project(projectB.id), {
        expected_context_revision: 2,
        description_sections: { goal: "Моя цель Беты" },
    });
});

it("запоздалый success Альфы не подменяет черновик Беты и не публикует её уведомление", async () => {
    const { client, patch } = setup();
    const pending = deferred<Project>();
    patch.mockReturnValueOnce(pending.promise);
    changeGoal("Отправленная цель Альфы");
    save();
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("link", { name: "К Бете" }));
    openPassport();
    changeGoal("Черновик Беты");
    await act(async () => pending.resolve({ ...projectA, context_revision: 3,
        description_sections: { ...projectA.description_sections!, goal: "Отправленная цель Альфы" } }));
    await waitFor(() => expect(client.isMutating()).toBe(0));
    expect(field("Название").value).toBe("Бета");
    expect(field("Цель").value).toBe("Черновик Беты");
    expect(toast.success).not.toHaveBeenCalled();
    save();
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(2));
    expect(patch).toHaveBeenLastCalledWith(endpoints.project(projectB.id), expect.objectContaining({
        expected_context_revision: 2, description_sections: expect.objectContaining({ goal: "Черновик Беты" }),
    }));
});

it("запоздалый 409 Альфы не запускает чтение конфликта и не блокирует форму Беты", async () => {
    const { client, get, patch } = setup();
    const pending = deferred<Project>();
    patch.mockReturnValueOnce(pending.promise);
    changeGoal("Цель Альфы в полёте");
    save();
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("link", { name: "К Бете" }));
    openPassport();
    changeGoal("Независимый черновик Беты");
    await act(async () => pending.reject(new ApiError(409, "Паспорт изменился")));
    await waitFor(() => expect(client.isMutating()).toBe(0));
    expect(get).not.toHaveBeenCalledWith(endpoints.project(projectA.id));
    expect(field("Цель").value).toBe("Независимый черновик Беты");
    expect(screen.queryByRole("button", { name: "Продолжить с моими правками" })).toBeNull();
    expect((screen.getByRole("button", { name: /^Сохранить$/ }) as HTMLButtonElement).disabled).toBe(false);
    expect(toast.error).not.toHaveBeenCalled();
});

it("ответ чтения конфликта Альфы после переключения проекта не появляется в Бете", async () => {
    const { client, get, patch } = setup();
    const latest = deferred<Project>();
    get.mockImplementation(async path => path === endpoints.project(projectA.id) ? latest.promise : []);
    patch.mockRejectedValueOnce(new ApiError(409, "Паспорт изменился"));
    changeGoal("Моя цель Альфы");
    save();
    await waitFor(() => expect(get).toHaveBeenCalledWith(endpoints.project(projectA.id)));
    fireEvent.click(screen.getByRole("link", { name: "К Бете" }));
    openPassport();
    changeGoal("Моя цель Беты");
    await act(async () => latest.resolve({ ...projectA, name: "Актуальная Альфа", context_revision: 3 }));
    await waitFor(() => expect(client.isMutating()).toBe(0));
    expect(field("Название").value).toBe("Бета");
    expect(field("Цель").value).toBe("Моя цель Беты");
    expect(screen.queryByText("Актуальный паспорт")).toBeNull();
    expect(toast.error).not.toHaveBeenCalled();
});

it.each(["Продолжить с моими правками", "Принять актуальную версию"])(
    "сохраняет черновик после 409 до явного выбора «%s»", async choice => {
        const { get, patch } = setup();
        const latest: Project = { ...projectA, name: "Актуальное название", context_revision: 3,
            description_md: "Актуальный паспорт Альфы", description_sections: { ...projectA.description_sections!,
                goal: "Чужая цель", capabilities: "Работающий каталог" } };
        get.mockImplementation(async path => path === endpoints.project(projectA.id) ? latest : []);
        patch.mockRejectedValueOnce(new ApiError(409, "Паспорт изменился"));
        fireEvent.change(field("Название"), { target: { value: " Альфа " } });
        changeGoal("Моя цель");
        save();
        await screen.findByRole("button", { name: choice });
        expect(field("Цель").value).toBe("Моя цель");
        expect(field("Название").value).toBe(" Альфа ");
        expect(patch.mock.calls[0][1]).not.toHaveProperty("name");
        expect((screen.getByRole("button", { name: /^Сохранить$/ }) as HTMLButtonElement).disabled).toBe(true);
        fireEvent.click(screen.getByRole("button", { name: choice }));
        expect(field("Название").value).toBe("Актуальное название");
        expect(field("Текущие возможности").value).toBe("Работающий каталог");
        expect(field("Цель").value).toBe(choice === "Продолжить с моими правками" ? "Моя цель" : "Чужая цель");
        expect(patch).toHaveBeenCalledTimes(1);
        if (choice === "Продолжить с моими правками") {
            save();
            await waitFor(() => expect(patch).toHaveBeenCalledTimes(2));
            expect(patch).toHaveBeenLastCalledWith(endpoints.project(projectA.id), {
                expected_context_revision: 3, description_sections: { goal: "Моя цель" },
            });
            expect(patch.mock.calls[1][1]).not.toHaveProperty("name");
        } else {
            expect((screen.getByRole("button", { name: /^Сохранить$/ }) as HTMLButtonElement).disabled).toBe(true);
        }
    },
);
