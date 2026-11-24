// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { api, ApiError } from "@/lib/api";
import * as timezone from "@/lib/timezone";
import type { TimelineEvent } from "@/lib/useProjectHistory";
import { useUiStore } from "@/stores/ui";
import { ChatContext, type ChatContextValue } from "@/lib/chatContext";
import { ChatContextPanel } from "./ChatContextPanel";
import { ChatHistoryContext } from "./ChatHistoryContext";

const clients: QueryClient[] = [];
const event: TimelineEvent = { id: "task_activity:1", kind: "task_activity", title: "Срок задачи изменён", detail: "Причина изменения", created_at: "2026-11-20T10:00:00Z", actor_name: "Анна", task_id: 42, from_value: "2026-11-20", to_value: "2026-11-23", event_type: "DUE_DATE_CHANGED" };
function setup(panel?: ChatContextValue) {
    vi.spyOn(timezone, "browserTimezone").mockReturnValue("Europe/Samara");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    clients.push(client);
    const onOpenFull = vi.fn();
    const tree = (value = panel) => <QueryClientProvider client={client}><MemoryRouter>{value
        ? <ChatContext.Provider value={value}><ChatContextPanel hidden={false} /></ChatContext.Provider>
        : <ChatHistoryContext projectId={1} projectKey="DEMO" onOpenFull={onOpenFull} />}</MemoryRouter></QueryClientProvider>;
    const view = render(tree());
    return { ...view, client, onOpenFull, select: (value: ChatContextValue) => view.rerender(tree(value)) };
}
afterEach(() => {
    cleanup(); clients.splice(0).forEach(client => client.clear());
    useUiStore.getState().setSelectedTaskId(null);
    vi.restoreAllMocks(); vi.useRealTimers();
});

it("reads one bounded page for the exact local day, renders changes and links the same period", async () => {
    vi.useFakeTimers({ toFake: ["Date"] }); vi.setSystemTime(new Date("2026-11-19T22:00:00Z"));
    const get = vi.spyOn(api, "get").mockResolvedValue({ items: [event], next_offset: 20 });
    setup();
    await screen.findByText(event.title);
    expect(get).toHaveBeenCalledTimes(1);
    const query = new URL(get.mock.calls[0][0], "http://local").searchParams;
    expect(Object.fromEntries(query)).toEqual({ since: "2026-11-20", until: "2026-11-20", timezone: "Europe/Samara", limit: "20", offset: "0" });
    expect(screen.getByText("14:00")).toBeTruthy(); expect(screen.getByText("Анна")).toBeTruthy();
    expect(screen.getByText("23.11.2026")).toBeTruthy();
    const link = new URL(screen.getByRole("link", { name: "Вся история за этот день" }).getAttribute("href")!, "http://local");
    expect(link.pathname).toBe("/projects/DEMO/knowledge");
    expect(Object.fromEntries(link.searchParams)).toEqual({ view: "history", since: "2026-11-20", until: "2026-11-20", timezone: "Europe/Samara" });
    get.mockResolvedValue({ items: [{ ...event, id: "task_activity:2", title: "Более раннее событие", created_at: "2026-11-20T09:00:00Z" }], next_offset: null });
    fireEvent.click(screen.getByRole("button", { name: "Загрузить более ранние события" }));
    await screen.findByText("Более раннее событие");
    expect(get.mock.calls[1][0]).toContain("offset=20");
    expect(within(screen.getByRole("list", { name: "События за сегодня" })).getAllByRole("listitem").map(item => item.textContent)).toEqual([expect.stringContaining(event.title), expect.stringContaining("Более раннее событие")]);
});

it("renders a deleted source snapshot without task or document navigation", async () => {
    vi.spyOn(api, "get").mockResolvedValue({ items: [{ ...event, kind: "project_context", title: "Изменён паспорт проекта", detail: "Сохранённая цель проекта", source_available: false, task_id: null, comment_id: null, report_id: null, document_ref: null }], next_offset: null });
    setup();
    await screen.findByText("Изменён паспорт проекта");
    expect(screen.getByText("Историческая запись")).toBeTruthy();
    expect(screen.getByText("Сохранённая цель проекта")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Открыть/ })).toBeNull();
    expect(screen.queryByRole("link", { name: "Открыть документ" })).toBeNull();
});

it.each([401, 403, 404])("hides all previously loaded contents after a %s continuation failure and retries", async status => {
    const get = vi.spyOn(api, "get").mockResolvedValue({ items: [event], next_offset: 20 });
    setup(); await screen.findByText(event.title);
    get.mockRejectedValue(new ApiError(status, "private error"));
    fireEvent.click(screen.getByRole("button", { name: "Загрузить более ранние события" }));
    await screen.findByText("Проект недоступен или у вас нет доступа к истории.");
    expect(screen.queryByText(event.title)).toBeNull(); expect(screen.queryByText("private error")).toBeNull();
    get.mockResolvedValue({ items: [], next_offset: null });
    fireEvent.click(screen.getByRole("button", { name: "Повторить загрузку" }));
    await screen.findByText("Сегодня событий пока нет.");
});

it("does not reveal cached history before a fresh read on remount or after a refetch denial", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({ items: [event], next_offset: null });
    const { client, unmount } = setup(); await screen.findByText(event.title); unmount();
    let reject!: (reason: Error) => void;
    get.mockImplementation(() => new Promise((_resolve, failure) => { reject = failure; }));
    render(<QueryClientProvider client={client}><MemoryRouter><ChatHistoryContext projectId={1} projectKey="DEMO" /></MemoryRouter></QueryClientProvider>);
    expect(screen.queryByText(event.title)).toBeNull();
    await act(async () => reject(new ApiError(403, "denied")));
    await screen.findByText("Проект недоступен или у вас нет доступа к истории.");
    expect(screen.queryByText(event.title)).toBeNull();
});

it.each([
    { comment_id: 12, label: "Открыть комментарий", target: { section: "comments", commentId: 12 } },
    { report_id: 91, report_revision: 3, label: "Открыть версию отчёта", target: { section: "reports", reportId: 91, reportRevision: 3 } },
])("opens exact navigation target $label", async ({ label, target, ...fields }) => {
    vi.spyOn(api, "get").mockResolvedValue({ items: [{ ...event, ...fields, document_ref: "opaque/ref" }], next_offset: null });
    const { onOpenFull } = setup(); await screen.findByText(event.title);
    fireEvent.click(screen.getByRole("button", { name: label }));
    expect(useUiStore.getState().selectedTaskId).toBe(42);
    expect(useUiStore.getState().taskNavigationTarget).toEqual(target);
    expect(onOpenFull).toHaveBeenCalledOnce();
    expect(screen.getByRole("link", { name: "Открыть документ" }).getAttribute("href")).toBe("/projects/DEMO/docs/opaque%2Fref");
});

it("advances an open day at midnight and after focus without displaying the previous day's data", async () => {
    vi.useFakeTimers({ toFake: ["Date"] }); vi.setSystemTime(new Date("2026-11-20T19:59:59Z"));
    const get = vi.spyOn(api, "get").mockResolvedValue({ items: [event], next_offset: null });
    setup(); await screen.findByText(event.title);
    get.mockResolvedValue({ items: [], next_offset: null });
    vi.setSystemTime(new Date("2026-11-20T20:00:00Z"));
    await waitFor(() => expect(get.mock.calls.at(-1)![0]).toContain("since=2026-11-21"), { timeout: 2000 });
    await screen.findByText("Сегодня событий пока нет.");
    expect(screen.queryByText(event.title)).toBeNull();
    vi.setSystemTime(new Date("2026-11-22T10:00:00Z")); fireEvent.focus(window);
    await waitFor(() => expect(get.mock.calls.at(-1)![0]).toContain("since=2026-11-22"));
});

it("allows today alongside a pinned selection, keeps pin state, and returns to a new selection", async () => {
    vi.spyOn(api, "get").mockResolvedValue({ items: [], next_offset: null });
    const panel: ChatContextValue = { projectId: 1, projectKey: "DEMO", desktop: true, isOpen: true, target: null, selectionKey: "empty", recent: [], pinned: true, select: vi.fn(), show: vi.fn(), close: vi.fn(), togglePin: vi.fn() };
    const { select } = setup(panel); await screen.findByText("Сегодня событий пока нет.");
    const selected: ChatContextValue = { ...panel, target: { type: "task", id: 42, title: "Task" }, selectionKey: "pick:1" };
    // Loading the task fails without exposing any old event contents; mode switching is independent.
    select(selected);
    fireEvent.click(screen.getByRole("button", { name: "Сегодня" }));
    await screen.findByText("Сегодня событий пока нет.");
    expect(screen.getByRole("button", { name: "Открепить контекст" }).getAttribute("aria-pressed")).toBe("true");
    select({ ...selected, selectionKey: "pick:2" });
    expect(screen.queryByRole("region", { name: "История проекта за сегодня" })).toBeNull();
    expect(screen.getByRole("button", { name: "Выбранный объект" }).getAttribute("aria-pressed")).toBe("true");
});
