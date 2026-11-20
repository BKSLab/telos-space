// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import { api } from "@/lib/api";
import { useUiStore } from "@/stores/ui";
import { ProjectHistoryTimeline } from "./ProjectHistoryTimeline";
import { historyDayKey, historyPeriod, historyValue } from "@/lib/projectHistory";

const clients: QueryClient[] = [];
const event = { id: "task_report:91", kind: "task_report", title: "Отчёт · Импорт · v2", detail: "Импорт реализован", created_at: "2026-11-20T21:30:00Z", actor_name: "Иван Петров", actor_user_id: 3, task_id: 12, report_id: 91, report_revision: 2 };
function Location() { return <output data-testid="url">{useLocation().search}</output>; }
function setup(search = "?view=history") {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    clients.push(client);
    const get = vi.spyOn(api, "get").mockImplementation(async path => {
        if (path.includes("/members")) return [{ user: { id: 3, username: "ivan", first_name: "Иван", last_name: "Петров", middle_name: null, has_avatar: false } }];
        if (path.endsWith("/tasks")) return [{ id: 12, title: "Импорт" }];
        return { items: [event], next_offset: null };
    });
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/projects/DEMO/knowledge${search}`]}><ProjectHistoryTimeline projectId={7} projectKey="DEMO" /><Location /></MemoryRouter></QueryClientProvider>);
    return get;
}
beforeEach(() => { vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} }); });
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("uses the same timezone for local day grouping and inclusive calendar shortcuts", () => {
    expect(historyDayKey(new Date("2026-11-20T21:30:00Z"), "Europe/Samara")).toBe("2026-11-21");
    expect(historyPeriod(7, "America/New_York", new Date("2026-03-10T15:00:00Z"))).toEqual({ since: "2026-03-04", until: "2026-03-10" });
});

it("formats known change values without exposing priority enums", () => {
    expect(historyValue("URGENT", "PRIORITY_CHANGED")).toBe("Срочный");
    expect(historyValue("IN_PROGRESS", "STAGE_CHANGED")).toBe("В работе");
    expect(historyValue("2026-11-20", "DUE_DATE_CHANGED")).toBe("20.11.2026");
    expect(historyValue(null)).toBe("Не задано");
});

it("keeps map parameters when removing a history filter and resets pagination", async () => {
    const get = setup("?view=history&mq=решение&category=decisions&q=Импорт&kind=task_report&offset=20&timezone=Europe%2FSamara");
    await screen.findByText(event.title);
    expect(get).toHaveBeenCalledWith(expect.stringContaining("timezone=Europe%2FSamara"));
    expect(screen.getByRole("heading", { name: /21 ноября/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Убрать фильтр: Поиск: Импорт" }));
    const url = new URLSearchParams(screen.getByTestId("url").textContent!);
    expect(url.get("q")).toBeNull(); expect(url.get("offset")).toBeNull();
    expect(url.get("mq")).toBe("решение"); expect(url.get("category")).toBe("decisions"); expect(url.get("kind")).toBe("task_report");
});

it("opens a linked event independently of list filters and restores the list", async () => {
    const get = setup("?view=history&event=task_report%3A91&q=другое&offset=20");
    await screen.findByText(event.title);
    const query = new URL(get.mock.calls.find(([path]) => path.includes("/history"))![0], "https://example.org").searchParams;
    expect(query.get("event_id")).toBe("task_report:91"); expect(query.has("q")).toBe(false); expect(query.get("offset")).toBe("0");
    fireEvent.click(screen.getByRole("button", { name: "Вернуться к ленте" }));
    await waitFor(() => expect(get).toHaveBeenCalledWith(expect.stringContaining("q=%D0%B4%D1%80%D1%83%D0%B3%D0%BE%D0%B5")));
    expect(new URLSearchParams(screen.getByTestId("url").textContent!).get("offset")).toBe("20");
});

it("opens the exact published report rather than the latest report", async () => {
    setup(); await screen.findByText(event.title);
    fireEvent.click(screen.getByRole("button", { name: "Открыть версию отчёта" }));
    expect(useUiStore.getState().selectedTaskId).toBe(12);
    expect(useUiStore.getState().taskNavigationTarget).toMatchObject({ section: "reports", reportId: 91, reportRevision: 2 });
});

it("selects an exact author and task and sends their IDs", async () => {
    const get = setup(); await screen.findByText(event.title);
    fireEvent.click(screen.getByRole("button", { name: "Автор, задача и период" }));
    await screen.findByRole("option", { name: "Импорт" });
    fireEvent.click(screen.getByRole("button", { name: /^Автор$/ }));
    fireEvent.click(await screen.findByRole("option", { name: "Петров Иван" }));
    fireEvent.change(screen.getByLabelText("Задача", { exact: true }), { target: { value: "12" } });
    fireEvent.click(screen.getByRole("button", { name: "Найти" }));
    await waitFor(() => expect(get).toHaveBeenCalledWith(expect.stringMatching(/actor_user_id=3&task_id=12/)));
});
