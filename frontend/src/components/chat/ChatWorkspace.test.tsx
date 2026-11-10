// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Link, MemoryRouter } from "react-router-dom";
import type { ReactNode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { queryKeys } from "@/lib/api";
import type { Project } from "@/lib/types";
import { useChatDockStore, type ChatPanel } from "@/stores/chatDock";
import { ChatWorkspace } from "./ChatWorkspace";

vi.mock("@/lib/useAuth", () => ({ useCurrentUser: () => ({ data: { id: 1 } }) }));
vi.mock("./FloatingChatDock", () => ({ FloatingChatDock: ({ project, routeProject, fullPage, panel }: { project?: Project; routeProject?: Project; fullPage: boolean; panel: ChatPanel | null }) => <output data-testid="dock" data-project={project?.id} data-route-project={routeProject?.id} data-full-page={String(fullPage)} data-panel={panel ?? "closed"} /> }));
vi.mock("./ProjectChatProvider", () => ({ ProjectChatProvider: ({ projectId, chatVisible, children }: { projectId: number; chatVisible: boolean; children: ReactNode }) => <><output data-testid="provider" data-project={projectId}>{String(chatVisible)}</output>{children}</> }));

const projects = [{ id: 1, key: "DEMO", name: "Учебный проект" }, { id: 2, key: "SAMPLE", name: "Личный план" }] as Project[];
const clients: QueryClient[] = [];
function setup(path: string, availableProjects = projects) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    clients.push(client);
    client.setQueryData(queryKeys.projects, availableProjects);
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><ChatWorkspace>
        <p>Содержимое</p><Link to="/projects/SAMPLE/team">Другой проект</Link><Link to="/?view=projects">Список проектов</Link>
    </ChatWorkspace></MemoryRouter></QueryClientProvider>);
}

beforeEach(() => useChatDockStore.setState({ panel: null, projects: {}, modes: {} }));
afterEach(() => { cleanup(); clients.splice(0).forEach((client) => client.clear()); });

it.each([
    ["/projects/DEMO/chat", true, true],
    ["/projects/DEMO/conversations", false, true],
    ["/projects/DEMO/knowledge", false, true],
    ["/projects/DEMO/team", false, false],
    ["/chat", false, true],
] as const)("передаёт полноэкранный режим и видимость общего чата: %s", (path, visible, fullPage) => {
    setup(path);
    expect(screen.getAllByTestId("provider")).toHaveLength(1);
    expect(screen.getByTestId("provider").textContent).toBe(String(visible));
    expect(screen.getByTestId("dock").dataset.fullPage).toBe(String(fullPage));
});

it("не меняет выбранный проект открытого чата при переходе на другую страницу", async () => {
    useChatDockStore.setState({ panel: "team", projects: { 1: 1 }, modes: { 1: "team" } });
    setup("/projects/DEMO/tasks");
    expect(screen.getByTestId("provider").dataset.project).toBe("1");
    expect(screen.getByTestId("provider").textContent).toBe("true");
    fireEvent.click(screen.getByRole("link", { name: "Другой проект" }));
    await waitFor(() => expect(screen.getByTestId("dock").dataset.routeProject).toBe("2"));
    expect(screen.getByTestId("dock").dataset.project).toBe("1");
    expect(screen.getByTestId("provider").dataset.project).toBe("1");
    expect(useChatDockStore.getState().projects[1]).toBe(1);
    fireEvent.click(screen.getByRole("link", { name: "Список проектов" }));
    await waitFor(() => expect(screen.getByTestId("dock").dataset.routeProject).toBeUndefined());
    expect(screen.getAllByTestId("provider")).toHaveLength(1);
    expect(screen.getByTestId("provider").dataset.project).toBe("1");
});

it("не переключает открытый личный диалог на общий при переходе в проект", async () => {
    useChatDockStore.setState({ panel: "personal", projects: { 1: 1 }, modes: { 1: "personal" } });
    setup("/");
    fireEvent.click(screen.getByRole("link", { name: "Другой проект" }));
    await waitFor(() => expect(screen.getByTestId("dock").dataset.routeProject).toBe("2"));
    expect(screen.getByTestId("dock").dataset.panel).toBe("personal");
    expect(screen.getByTestId("provider").textContent).toBe("false");
    expect(useChatDockStore.getState().modes[1]).toBe("personal");
});

it("показывает единый вход в чаты и пользователю без проектов", () => {
    setup("/", []);
    expect(screen.getByTestId("dock").dataset.project).toBeUndefined();
    expect(screen.getByTestId("dock").dataset.fullPage).toBe("false");
});

it("закрывает плавающее окно при входе на полную страницу чата", async () => {
    useChatDockStore.setState({ panel: "personal", projects: { 1: 2 }, modes: { 1: "personal" } });
    setup("/projects/DEMO/chat");
    await waitFor(() => expect(useChatDockStore.getState().panel).toBeNull());
    expect(screen.getByTestId("provider").dataset.project).toBe("1");
    expect(screen.getByTestId("provider").textContent).toBe("true");
});
