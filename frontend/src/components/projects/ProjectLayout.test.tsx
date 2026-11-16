// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { queryKeys } from "@/lib/api";
import { ProjectLayout } from "./ProjectLayout";

vi.mock("@/lib/useProject", () => ({
    useProjectFromRoute: () => ({ project: { id: 1, key: "DEMO", name: "Личный проект", color: "#7299cc", icon: null, status: "ACTIVE",
        description_md: "**Доступный** [терминал](/guide) для `разработки`.\n\n- Без лишней разметки", description_sections: null }, projectKey: "DEMO", isPending: false, isMissing: false }),
}));
vi.mock("@/components/chat/ChatUnreadBadge", () => ({ ChatUnreadBadge: () => <span>2</span> }));

beforeEach(() => {
    vi.stubGlobal("ResizeObserver", class {
        observe = vi.fn();
        disconnect = vi.fn();
    });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function setup(path = "/projects/DEMO/chat") {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    client.setQueryData(queryKeys.projectStats(1), { total_tasks: 1, in_progress_tasks: 1, done_tasks: 0, overdue_tasks: 0, completion_rate: 0 });
    const result = render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}>
        <Routes><Route path="/projects/:projectKey" element={<ProjectLayout />}>
            <Route index element={<p>Состояние проекта</p>} />
            <Route path="chat" element={<p>Общая переписка</p>} />
            <Route path="settings" element={<p>Параметры проекта</p>} />
        </Route></Routes>
    </MemoryRouter></QueryClientProvider>);
    return { client, ...result };
}

it("оставляет одну активную вкладку чата и сохраняет остальные разделы проекта", () => {
    const { client } = setup();
    const navigation = within(screen.getByRole("navigation", { name: "Разделы проекта" }));
    const chat = navigation.getByRole("link", { name: /Чат/ });
    expect(chat.getAttribute("href")).toBe("/projects/DEMO/chat");
    expect(chat.getAttribute("aria-current")).toBe("page");
    expect(navigation.getAllByRole("link", { name: /Чат/ })).toHaveLength(1);
    const tabs = navigation.getAllByRole("link");
    expect(tabs[0]).toBe(navigation.getByRole("link", { name: "Обзор" }));
    expect(tabs[1]).toBe(chat);
    expect(navigation.getByRole("link", { name: "Обзор" }).getAttribute("href")).toBe("/projects/DEMO");
    expect(navigation.queryByRole("link", { name: "Пульс" })).toBeNull();
    expect(navigation.queryByRole("link", { name: "Агент" })).toBeNull();
    expect(navigation.getByRole("link", { name: "Структура" }).getAttribute("href")).toBe("/projects/DEMO/structure");
    expect(navigation.getByRole("link", { name: "Задачи" }).getAttribute("href")).toBe("/projects/DEMO/tasks");
    expect(navigation.queryByRole("link", { name: "Канбан" })).toBeNull();
    expect(screen.getByText("Общая переписка")).toBeTruthy();
    client.clear();
});

it("показывает крупную identity с читаемым описанием только в обзоре", async () => {
    const { client, container } = setup("/projects/demo/");
    expect(container.querySelector(".project-overview-header")).toBeTruthy();
    expect(container.querySelector(".project-overview-logo")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Личный проект" })).toBeTruthy();
    expect(screen.getByText("DEMO")).toBeTruthy();
    expect(screen.getByText("В работе")).toBeTruthy();
    const description = await screen.findByText("Доступный терминал для разработки. Без лишней разметки");
    expect(description.querySelector("a, code, strong, ul")).toBeNull();
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.getByRole("link", { name: "Настройки проекта" }).getAttribute("href")).toBe("/projects/DEMO/settings");
    client.clear();
});

it("при переходе с обзора оставляет компактную шапку и доступные с клавиатуры действия", async () => {
    const user = userEvent.setup();
    const { client, container } = setup("/projects/DEMO");
    const settings = screen.getByRole("link", { name: "Настройки проекта" });
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("group", { name: "Сведения о проекте" }));
    await user.tab();
    expect(document.activeElement).toBe(settings);
    await user.keyboard("{Enter}");
    expect(screen.getByText("Параметры проекта")).toBeTruthy();
    expect(container.querySelector(".project-overview-header")).toBeNull();
    expect(container.querySelector(".project-overview-logo")).toBeNull();
    expect(screen.getByTestId("project-icon-default").style.width).toBe("var(--project-icon-size, 32px)");
    expect(screen.queryByText("Доступный терминал для разработки. Без лишней разметки")).toBeNull();
    expect(screen.getByRole("progressbar")).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "Разделы проекта" })).toBeTruthy();
    client.clear();
});
