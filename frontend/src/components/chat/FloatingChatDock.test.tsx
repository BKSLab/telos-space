// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Project } from "@/lib/types";
import { useChatDockStore } from "@/stores/chatDock";
import { FloatingChatDock } from "./FloatingChatDock";

vi.mock("@/lib/useProjectChat", () => ({ useProjectChat: () => ({ info: { unread_count: 2 }, error: null }) }));
vi.mock("@/lib/useAuth", () => ({ useCurrentUser: () => ({ data: { id: 1 } }) }));
vi.mock("./ProjectChatWorkspace", () => ({ ProjectChatWorkspace: () => <p>Общий чат</p> }));
vi.mock("@/components/tim/TimPanel", () => ({ TimPanel: ({ embedded }: { embedded?: boolean }) => <section role={embedded ? undefined : "dialog"} aria-label="Личный виджет Ариса" data-testid="personal" data-embedded={String(embedded)}>Личный диалог Ариса</section> }));

const project = { id: 1, key: "DEMO", name: "Личный проект" } as Project;
const anotherProject = { id: 2, key: "SAMPLE", name: "Другой проект" } as Project;
const projects = [project, anotherProject];
function Location() {
    const location = useLocation();
    return <output data-testid="location">{location.pathname}{location.search}{location.hash}</output>;
}
function Dock({ routeProject = project, userId = 1, fullPage = false, availableProjects = projects }: { routeProject?: Project | null; userId?: number; fullPage?: boolean; availableProjects?: Project[] }) {
    const panel = useChatDockStore((state) => state.panel);
    const selectedId = useChatDockStore((state) => state.projects[userId]);
    const selectedProject = availableProjects.find((item) => item.id === selectedId) ?? routeProject ?? undefined;
    return <FloatingChatDock project={selectedProject} routeProject={routeProject ?? undefined} projects={availableProjects} userId={userId} fullPage={fullPage} panel={panel} />;
}
function setup(props: Parameters<typeof Dock>[0] = {}, path = "/projects/DEMO/board?task=42&confirm=action-id#context") {
    render(<MemoryRouter initialEntries={[path]}><Dock {...props} /><Location /></MemoryRouter>);
}

beforeEach(() => {
    useChatDockStore.setState({ panel: null, projects: {}, modes: {} });
});
afterEach(cleanup);

it("открывает оба вида чата из одной компактной кнопки и одного окна", async () => {
    setup();
    expect(screen.queryByRole("button", { name: "Открыть чат агента" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Открыть чат команды" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Спросить Ариса" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Выбор чата" })).toBeNull();
    expect(screen.getAllByRole("button", { name: "Открыть чаты" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Открыть чаты" }));
    const dialog = await screen.findByRole("dialog", { name: "Чаты" });
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    const selector = screen.getByRole("combobox", { name: "Выбор чата" }) as HTMLSelectElement;
    expect(selector.value).toBe("project:1");
    expect(Array.from(selector.options).map((option) => [option.value, option.text])).toEqual([
        ["personal", "Личный — Арис"], ["project:1", "DEMO · Личный проект"], ["project:2", "SAMPLE · Другой проект"],
    ]);
    expect(screen.getByText("Общий чат")).toBeTruthy();
    fireEvent.change(selector, { target: { value: "personal" } });
    await screen.findByText("Личный диалог Ариса");
    expect(screen.getByTestId("personal").dataset.embedded).toBe("true");
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    expect(screen.queryByText("Общий чат")).toBeNull();
    expect(useChatDockStore.getState().modes[1]).toBe("personal");
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Чаты" })).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "Открыть чаты" })));
});

it("разворачивает командный чат, сохраняя query и hash исходной страницы", async () => {
    setup();
    fireEvent.click(screen.getByRole("button", { name: "Открыть чаты" }));
    fireEvent.click(screen.getByRole("button", { name: "Открыть чат во вкладке" }));
    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe("/projects/DEMO/chat?task=42&confirm=action-id#context"));
    expect(useChatDockStore.getState().panel).toBeNull();
});

it("вне проекта начинает с личного чата и затем помнит выбранный общий чат", async () => {
    setup({ routeProject: null }, "/projects");
    const button = screen.getByRole("button", { name: "Открыть чаты" });
    fireEvent.click(button);
    await screen.findByText("Личный диалог Ариса");
    const selector = screen.getByRole("combobox", { name: "Выбор чата" }) as HTMLSelectElement;
    expect(selector.value).toBe("personal");
    fireEvent.change(selector, { target: { value: "project:2" } });
    await screen.findByText("Общий чат");
    expect(useChatDockStore.getState().projects[1]).toBe(2);
    expect(useChatDockStore.getState().modes[1]).toBe("team");
    fireEvent.click(screen.getByRole("button", { name: "Свернуть чат" }));
    fireEvent.click(screen.getByRole("button", { name: "Открыть чаты" }));
    await waitFor(() => expect((screen.getByRole("combobox", { name: "Выбор чата" }) as HTMLSelectElement).value).toBe("project:2"));
});

it("разворачивает личный диалог в отдельную страницу /chat", async () => {
    setup({ routeProject: null }, "/my-tasks");
    fireEvent.click(screen.getByRole("button", { name: "Открыть чаты" }));
    await screen.findByText("Личный диалог Ариса");
    fireEvent.click(screen.getByRole("button", { name: "Открыть чат во вкладке" }));
    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe("/chat"));
    expect(useChatDockStore.getState().panel).toBeNull();
});

it("из проекта также раскрывает личный диалог на /chat с прежними query и hash", async () => {
    setup();
    fireEvent.click(screen.getByRole("button", { name: "Открыть чаты" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Выбор чата" }), { target: { value: "personal" } });
    await screen.findByText("Личный диалог Ариса");
    fireEvent.click(screen.getByRole("button", { name: "Открыть чат во вкладке" }));
    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe("/chat?task=42&confirm=action-id#context"));
    expect(useChatDockStore.getState().panel).toBeNull();
});

it("выбирает сохранённый чат текущего пользователя отдельно от другого аккаунта", async () => {
    useChatDockStore.setState({ projects: { 1: 1, 2: 2 }, modes: { 1: "team", 2: "personal" } });
    setup({ routeProject: null, userId: 2 }, "/projects");
    fireEvent.click(screen.getByRole("button", { name: "Открыть чаты" }));
    await screen.findByText("Личный диалог Ариса");
    expect((screen.getByRole("combobox", { name: "Выбор чата" }) as HTMLSelectElement).value).toBe("personal");
    expect(useChatDockStore.getState().projects[1]).toBe(1);
    expect(useChatDockStore.getState().modes[1]).toBe("team");
});

it.each(["team", "personal"] as const)("на полной странице скрывает вход и окно, даже если сохранена открытая панель %s", (panel) => {
    useChatDockStore.setState({ panel });
    setup({ fullPage: true });
    expect(screen.queryByRole("button", { name: "Открыть чаты" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Открыть чат команды" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Спросить Ариса" })).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
});
