// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { ProjectChatPage } from "./ProjectChatPage";

vi.mock("@/lib/useProjectOutlet", () => ({ useProjectOutlet: () => ({ id: 1, key: "DEMO", name: "Учебный проект" }) }));
vi.mock("@/components/chat/ProjectChatWorkspace", () => ({ ProjectChatWorkspace: () => <p>Общая переписка команды</p> }));
vi.mock("@/components/agent/ProjectAgentWorkspace", () => ({ ProjectAgentWorkspace: ({ knowledgeOnly }: { knowledgeOnly: boolean }) => <p>{knowledgeOnly ? "Диагностика индекса" : "Сохранённые личные диалоги"}</p> }));

function Location() {
    const location = useLocation();
    return <output data-testid="location">{location.pathname}{location.search}{location.hash}</output>;
}
function setup(path = "/projects/DEMO/chat?conversation=17&task=42&confirm=action-id#context") {
    render(<MemoryRouter initialEntries={[path]}><Routes>
        <Route path="/projects/:projectKey/chat" element={<ProjectChatPage />} />
        <Route path="/chat" element={<p>Полная личная переписка</p>} />
    </Routes><Location /></MemoryRouter>);
}

afterEach(cleanup);

it("открывает во вкладке проекта только общий чат без выбора переписки и дополнительных меню", () => {
    setup();
    expect(screen.getByText("Общая переписка команды")).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: "Выбор чата" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Личные диалоги и знания" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Проектные диалоги и знания" })).toBeNull();
    expect(screen.queryByText("Сохранённые личные диалоги")).toBeNull();
    expect(screen.queryByText("Диагностика индекса")).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
});

it.each([
    ["personal", "Сохранённые личные диалоги"],
    ["knowledge", "Диагностика индекса"],
])("сохраняет прежнее представление %s по прямой ссылке и возвращает в общий чат", async (view, text) => {
    setup(`/projects/DEMO/chat?conversation=17&task=42&confirm=action-id&view=${view}#context`);
    await screen.findByText(text);
    expect(screen.queryByText("Общая переписка команды")).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Выбор чата" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Личные диалоги и знания" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Общий чат" }));
    await waitFor(() => expect(screen.getByText("Общая переписка команды")).toBeTruthy());
    expect(screen.queryByText(text)).toBeNull();
    const url = new URL(screen.getByTestId("location").textContent!, "http://local");
    expect(url.pathname).toBe("/projects/DEMO/chat");
    expect(url.searchParams.has("view")).toBe(false);
    expect(url.searchParams.get("conversation")).toBe("17");
    expect(url.searchParams.get("task")).toBe("42");
    expect(url.searchParams.get("confirm")).toBe("action-id");
    expect(url.hash).toBe("#context");
});

it("перенаправляет прежний view=vera на /chat, удаляя только view", async () => {
    setup("/projects/DEMO/chat?conversation=17&view=vera&task=42&confirm=action-id&tag=a&tag=b#context");
    await screen.findByText("Полная личная переписка");
    expect(screen.queryByText("Общая переписка команды")).toBeNull();
    const url = new URL(screen.getByTestId("location").textContent!, "http://local");
    expect(url.pathname).toBe("/chat");
    expect(url.searchParams.has("view")).toBe(false);
    expect(url.searchParams.get("conversation")).toBe("17");
    expect(url.searchParams.get("task")).toBe("42");
    expect(url.searchParams.get("confirm")).toBe("action-id");
    expect(url.searchParams.getAll("tag")).toEqual(["a", "b"]);
    expect(url.hash).toBe("#context");
});
