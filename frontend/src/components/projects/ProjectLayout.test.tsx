// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { queryKeys } from "@/lib/api";
import { ProjectLayout } from "./ProjectLayout";

vi.mock("@/lib/useProject", () => ({
    useProjectFromRoute: () => ({ project: { id: 1, key: "DEMO", name: "Личный проект", color: "#7299cc", icon: null, status: "ACTIVE" }, projectKey: "DEMO", isPending: false, isMissing: false }),
}));
vi.mock("@/components/chat/ChatUnreadBadge", () => ({ ChatUnreadBadge: () => <span>2</span> }));

beforeEach(() => {
    vi.stubGlobal("ResizeObserver", class {
        observe = vi.fn();
        disconnect = vi.fn();
    });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("оставляет одну активную вкладку чата и сохраняет остальные разделы проекта", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    client.setQueryData(queryKeys.projectStats(1), { total_tasks: 1, in_progress_tasks: 1, done_tasks: 0, overdue_tasks: 0, completion_rate: 0 });
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/projects/DEMO/chat"]}>
        <Routes><Route path="/projects/:projectKey" element={<ProjectLayout />}><Route path="chat" element={<p>Общая переписка</p>} /></Route></Routes>
    </MemoryRouter></QueryClientProvider>);
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
    expect(screen.getByText("Общая переписка")).toBeTruthy();
    client.clear();
});
