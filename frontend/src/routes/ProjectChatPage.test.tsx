// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { ProjectChatPage } from "./ProjectChatPage";
import { ProjectConversationsPage } from "./ProjectConversationsPage";
import { ProjectKnowledgePage } from "./ProjectKnowledgePage";

vi.mock("@/lib/useProjectOutlet", () => ({ useProjectOutlet: () => ({ id: 1, key: "DEMO", name: "Учебный проект" }) }));
vi.mock("@/components/chat/ProjectChatWorkspace", () => ({ ProjectChatWorkspace: () => <p>Общая переписка команды</p> }));
vi.mock("@/components/agent/ProjectConversationWorkspace", () => ({ ProjectConversationWorkspace: () => <p>Сохранённые личные диалоги</p> }));
vi.mock("@/components/knowledge/ProjectKnowledgePanel", () => ({ ProjectKnowledgePanel: ({ projectId }: { projectId: number }) => <p>Диагностика индекса проекта {projectId}</p> }));

function Location() {
    const location = useLocation();
    return <output data-testid="location">{location.pathname}{location.search}{location.hash}</output>;
}
function setup(path = "/projects/DEMO/chat?conversation=17&task=42&confirm=action-id#context") {
    render(<MemoryRouter initialEntries={[path]}><Routes>
        <Route path="/projects/:projectKey/chat" element={<ProjectChatPage />} />
        <Route path="/projects/:projectKey/conversations" element={<ProjectConversationsPage />} />
        <Route path="/projects/:projectKey/knowledge" element={<ProjectKnowledgePage />} />
    </Routes><Location /></MemoryRouter>);
}

afterEach(cleanup);

it("открывает во вкладке проекта только общий чат без дополнительных меню", () => {
    setup();
    expect(screen.getByText("Общая переписка команды")).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: "Выбор чата" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Проектные диалоги и знания" })).toBeNull();
    expect(screen.queryByText("Сохранённые личные диалоги")).toBeNull();
    expect(screen.queryByText("Диагностика индекса проекта 1")).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
});

it.each([
    ["conversations", "Сохранённые личные диалоги", "Диагностика индекса проекта 1"],
    ["knowledge", "Диагностика индекса проекта 1", "Сохранённые личные диалоги"],
])("открывает самостоятельную страницу %s и сохраняет контекст при возвращении в общий чат", async (section, text, absent) => {
    const context = "?conversation=17&task=42&confirm=action-id&tag=a&tag=b#context";
    setup(`/projects/DEMO/${section}${context}`);
    await screen.findByText(text);
    expect(screen.queryByText(absent)).toBeNull();
    expect(screen.queryByText("Общая переписка команды")).toBeNull();
    expect(screen.getByTestId("location").textContent).toBe(`/projects/DEMO/${section}${context}`);
    fireEvent.click(screen.getByRole("link", { name: "Общий чат" }));
    await waitFor(() => expect(screen.getByText("Общая переписка команды")).toBeTruthy());
    expect(screen.queryByText(text)).toBeNull();
    expect(screen.getByTestId("location").textContent).toBe(`/projects/DEMO/chat${context}`);
});
