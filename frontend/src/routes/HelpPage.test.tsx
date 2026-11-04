// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HelpPage } from "./HelpPage";
import { integrationCatalog } from "@/lib/integrationCatalog";

function Location() { const location = useLocation(); return <output data-testid="location">{location.pathname}{location.search}{location.hash}</output>; }
function setup(path = "/help") {
    return render(<MemoryRouter initialEntries={[path]}><Location /><Routes><Route path="/help/:sectionId?" element={<HelpPage />} /></Routes></MemoryRouter>);
}
beforeEach(() => {
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => setTimeout(() => callback(0), 0));
    vi.stubGlobal("cancelAnimationFrame", clearTimeout);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("открывает общие сведения и тематические разделы с заголовками сущностей", async () => {
    setup();
    expect(screen.getByRole("heading", { name: "Общие сведения" })).toBeTruthy();
    const nav = within(screen.getByRole("navigation", { name: "Разделы справки" }));
    await userEvent.click(nav.getByRole("link", { name: "Проекты" }));
    expect(screen.getByRole("heading", { name: "Команда и доступ" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Структура работ — ИСР" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Риски" })).toBeTruthy();
});
it("ищет за пределами открытого раздела и переводит прямо к найденному заголовку", async () => {
    setup("/help/projects");
    await userEvent.type(screen.getByRole("searchbox", { name: "Поиск по справке" }), "итоговый отчет");
    const result = screen.getByRole("link", { name: /Завершение и итоговый отчёт/ });
    expect(result.getAttribute("href")).toBe("/help/tasks#task-completion");
    await userEvent.click(result);
    expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe("");
    expect(screen.getByTestId("location").textContent).toBe("/help/tasks#task-completion");
    await waitFor(() => expect(document.activeElement?.id).toBe("task-completion"));
});
it("объясняет пустой результат и очищает поиск", async () => {
    setup("/help/boards?q=несуществующийтермин");
    expect(screen.getByText("Ничего не найдено")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Сбросить поиск" }));
    expect(screen.getByRole("heading", { name: "Доски" })).toBeTruthy();
});
it("показывает настройки MCP с текущим адресом и плейсхолдером, без формы выпуска токена", () => {
    setup("/help/mcp");
    expect(screen.getByText(/YOUR_API_KEY/, { selector: "code" }).textContent).toContain(`${window.location.origin}/mcp`);
    expect(screen.queryByRole("button", { name: "Выпустить токен" })).toBeNull();
    expect(screen.getByRole("link", { name: "Открыть ключи доступа" }).getAttribute("href")).toBe("/profile?tab=keys");
});
it("позволяет выбрать раздел компактным контролом и обрабатывает неизвестные ссылки", async () => {
    setup("/help/unknown");
    expect(screen.getByText("Раздел справки не найден")).toBeTruthy();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Раздел справки" }), "analytics");
    expect(screen.getByRole("heading", { name: "Аналитика" })).toBeTruthy();
});

it("открывает найденный MCP-инструмент вместе с аргументами и правами", async () => {
    setup("/help/about?q=tt_create_task");
    const entry = integrationCatalog.mcp.find(item => item.name === "tt_create_task")!;
    const result = screen.getByRole("link", { name: `MCP · ${entry.name} · ${entry.summary}` });
    await userEvent.click(result);
    await waitFor(() => expect(document.activeElement?.id).toBe(entry.id));
    expect(screen.getByRole("heading", { name: "Аргументы" })).toBeTruthy();
    const access = screen.getByRole("region", { name: `Доступ ${entry.name}` });
    expect(access.textContent).toContain("tasks:write");
    expect(screen.getByText("project_key", { selector: "code" })).toBeTruthy();
});

it("фильтрует API по доступу и показывает сессионные методы без внешних ключей", async () => {
    setup("/help/api");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Фильтр каталога" }), "session");
    await userEvent.type(screen.getByRole("searchbox", { name: "Поиск методов API" }), "api-keys");
    const entry = integrationCatalog.api.find(item => item.method === "GET" && item.path === "/api/v1/users/me/api-keys")!;
    await userEvent.click(document.getElementById(entry.id)!);
    expect(screen.getByRole("region", { name: `Доступ ${entry.name}` }).textContent).toContain("Только сессия интерфейса");
    expect(screen.queryByText("tt_create_task", { selector: "code" })).toBeNull();
});
