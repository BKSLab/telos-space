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
let nextProjectId = 100;
const event = { id: "task_report:91", kind: "task_report", title: "Отчёт · Импорт · v2", detail: "Импорт реализован", created_at: "2026-11-20T09:30:00Z", actor_name: "Иван Петров", actor_user_id: 3, task_id: 12, report_id: 91, report_revision: 2 };
function Location() { return <output data-testid="url">{useLocation().search}</output>; }
function setup(search = "?view=history", items = [event], projectId = nextProjectId++) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    clients.push(client);
    const get = vi.spyOn(api, "get").mockImplementation(async path => {
        if (path.includes("/members")) return [{ user: { id: 3, username: "ivan", first_name: "Иван", last_name: "Петров", middle_name: null, has_avatar: false } }];
        if (path.endsWith("/tasks")) return [{ id: 12, title: "Импорт" }];
        return { items, next_offset: null };
    });
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/projects/DEMO/knowledge${search}`]}><ProjectHistoryTimeline projectId={projectId} projectKey="DEMO" /><Location /></MemoryRouter></QueryClientProvider>);
    return get;
}
beforeEach(() => { vi.useFakeTimers({ toFake: ["Date"] }); vi.setSystemTime(new Date("2026-11-20T12:00:00Z")); vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} }); });
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it("uses the same timezone for local day grouping and inclusive calendar shortcuts", () => {
    expect(historyDayKey(new Date("2026-11-20T21:30:00Z"), "Europe/Samara")).toBe("2026-11-21");
    expect(historyPeriod(7, "America/New_York", new Date("2026-03-10T15:00:00Z"))).toEqual({ since: "2026-03-04", until: "2026-03-10" });
});

it("сворачивает даты и раскрывает большой день порциями без потери событий", async () => {
    const items = Array.from({ length: 9 }, (_, index) => ({ ...event, id: `task_report:${index}`, title: `Публикация ${index}`, created_at: index === 8 ? "2026-11-19T10:00:00Z" : event.created_at }));
    setup("?view=history&timezone=UTC", items);
    await screen.findByText("Публикация 0");
    expect(screen.queryByText("Публикация 4")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Ещё 4 события за этот день" }));
    expect(screen.getByText("Публикация 7")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Свернуть 20 ноября/ }));
    expect(screen.getByText("Публикация 0").closest("[hidden]")).toBeTruthy();
    expect(screen.getByText("Публикация 8").closest("[hidden]")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Свернуть даты" }));
    expect(screen.getByText("Публикация 8").closest("[hidden]")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Развернуть даты" }));
    expect(screen.getByText("Публикация 7").closest("[hidden]")).toBeNull();
});

it("formats known change values without exposing priority enums", () => {
    expect(historyValue("URGENT", "PRIORITY_CHANGED")).toBe("Срочный");
    expect(historyValue("IN_PROGRESS", "STAGE_CHANGED")).toBe("В работе");
    expect(historyValue("2026-11-20", "DUE_DATE_CHANGED")).toBe("20.11.2026");
    expect(historyValue(null)).toBe("Не задано");
});

it("сохраняет раскрытую прошлую дату после ухода к документу и возврата", async () => {
    const projectId = nextProjectId++;
    const older = [{ ...event, created_at: "2026-11-19T10:00:00Z" }];
    setup("?view=history&timezone=UTC", older, projectId);
    fireEvent.click(await screen.findByRole("button", { name: /Развернуть 19 ноября/ }));
    cleanup();
    setup("?view=history&timezone=UTC", older, projectId);
    expect((await screen.findByRole("button", { name: /Свернуть 19 ноября/ })).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText(event.title).closest("[hidden]")).toBeNull();
});

it("keeps map parameters when removing a history filter and resets pagination", async () => {
    const get = setup("?view=history&mq=решение&category=decisions&q=Импорт&kind=task_report&offset=20&timezone=Europe%2FSamara");
    await screen.findByText(event.title);
    expect(get).toHaveBeenCalledWith(expect.stringContaining("timezone=Europe%2FSamara"));
    expect(screen.getByRole("heading", { name: /20 ноября/ })).toBeTruthy();
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
    expect(new URLSearchParams(screen.getByTestId("url").textContent!).get("offset")).toBeNull();
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
