// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError, api, queryKeys } from "@/lib/api";
import type { TaskDatesSave } from "@/components/tasks/TaskDatesDialog";
import type { CalendarTask, ScenarioPreview, TaskDependency } from "@/lib/types";
import { useGanttSchedule } from "./useGanttSchedule";

const task: CalendarTask = {
    id: 22, key: "TELOS-22", title: "Подготовить график", stage_id: 1, priority: "MEDIUM", assignee: null,
    start_date: "2026-11-16", due_date: "2026-11-18", baseline_start_date: "2026-11-16", baseline_due_date: "2026-11-18",
    drift_days: 0, is_done: false, is_overdue: false, is_due_soon: false, risk_level: null, risk_reasons: [],
    updated_at: "2026-11-16T12:00:00Z", revision: 7, wbs_node_id: null,
};
const nextDates = { startDate: "2026-11-17", dueDate: "2026-11-19" };
const dependency: TaskDependency = {
    id: 1, project_id: 1, predecessor_task_id: 22, successor_task_id: 23,
    dependency_type: "FINISH_TO_START", lag_days: 0, created_at: task.updated_at,
};

function preview(dueDate = nextDates.dueDate): ScenarioPreview {
    return {
        changes: [
            { task_id: 22, task_key: task.key, task_title: task.title, source: "DIRECT", reasons: [], expected_updated_at: task.updated_at,
                current: { start_date: task.start_date, due_date: task.due_date }, proposed: { start_date: nextDates.startDate, due_date: dueDate } },
            { task_id: 23, task_key: "TELOS-23", task_title: "Запустить проект", source: "CASCADE", reasons: [], expected_updated_at: "2026-11-16T13:00:00Z",
                current: { start_date: "2026-11-18", due_date: "2026-11-20" }, proposed: { start_date: dueDate, due_date: "2026-11-23" } },
        ], conflicts: [], consequences_count: 1, can_apply: true,
    };
}

function setup(options: { original?: CalendarTask; linked?: boolean; saveDates?: (taskId: number, dates: TaskDatesSave, expectedRevision: number) => Promise<unknown> } = {}) {
    const saveDates = options.saveDates ?? vi.fn().mockResolvedValue(undefined);
    const fixTaskBaseline = vi.fn().mockResolvedValue(undefined);
    const original = options.original ?? task;
    function Harness() {
        const schedule = useGanttSchedule({
            projectId: 1, dependencies: options.linked ? [dependency] : [], saveDates,
            isSavingDates: false, fixTaskBaseline, isFixingBaseline: false, createDependency: vi.fn(),
        });
        return <>
            <button onClick={() => schedule.changeDates(original, nextDates)}>Перенести</button>
            <button onClick={() => schedule.openDates(original)}>Изменить даты</button>
            {schedule.dialog}
        </>;
    }
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } });
    render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>);
    return { saveDates, fixTaskBaseline, client };
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); });

it("перенос старых дат ждёт явного сохранения с причиной; отмена не пишет данные", async () => {
    const post = vi.spyOn(api, "post");
    const { saveDates } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Перенести" }));
    expect((screen.getByLabelText("Завершение") as HTMLInputElement).value).toBe(nextDates.dueDate);
    expect(saveDates).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(saveDates).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Перенести" }));
    await userEvent.type(screen.getByRole("textbox", { name: /Причина/ }), "  Согласование заказчика  ");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить сроки" }));
    expect(saveDates).toHaveBeenCalledExactlyOnceWith(22, { ...nextDates, dateChangeReason: "Согласование заказчика" }, 7);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

it.each([
    { start_date: null, due_date: null },
    { start_date: null, due_date: nextDates.dueDate },
    { start_date: nextDates.startDate, due_date: null },
])("первое назначение отсутствующего поля ждёт Save без причины, отмена не пишет (%j)", async (dates) => {
    const { saveDates } = setup({ original: { ...task, ...dates } });
    await userEvent.click(screen.getByRole("button", { name: "Перенести" }));
    expect(screen.getByRole("dialog", { name: "Изменить сроки" })).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: /Причина/ })).toBeNull();
    expect(saveDates).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(saveDates).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Перенести" }));
    await userEvent.click(screen.getByRole("button", { name: "Сохранить сроки" }));
    expect(saveDates).toHaveBeenCalledExactlyOnceWith(22, nextDates, 7);
});

it("одно окно сохраняет каскад с общей причиной и версиями каждой задачи", async () => {
    const post = vi.spyOn(api, "post").mockImplementation(async (path) => path.endsWith("/preview") ? preview() : { applied_count: 2, task_ids: [22, 23] });
    const { saveDates } = setup({ linked: true });
    await userEvent.click(screen.getByRole("button", { name: "Изменить даты" }));
    fireEvent.change(screen.getByLabelText("Завершение"), { target: { value: nextDates.dueDate } });
    await screen.findByRole("checkbox", { name: "Сдвинуть зависимые задачи (1)" });
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    expect(post.mock.calls.filter(([path]) => path.endsWith("/apply"))).toHaveLength(0);
    await userEvent.type(screen.getByRole("textbox", { name: /Причина/ }), "  Поставка задержана  ");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить и сдвинуть" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(post).toHaveBeenLastCalledWith("/api/v1/projects/1/calendar/scenarios/apply", {
        date_change_reason: "Поставка задержана",
        changes: preview().changes.map(change => ({
            task_id: change.task_id,
            ...change.proposed,
            ...(change.task_id === task.id && { start_date: task.start_date }),
            expected_updated_at: change.expected_updated_at,
        })),
    });
    expect(saveDates).not.toHaveBeenCalled();
});

it.each([true, false])("свежий preview не повышает версию основной задачи (есть в changes: %s)", async (includeSource) => {
    const current = { start_date: task.start_date, due_date: task.due_date, updated_at: task.updated_at };
    const conflict = "Основная задача изменилась после открытия окна";
    const post = vi.spyOn(api, "post").mockImplementation(async (path, body) => {
        if (path.endsWith("/preview")) {
            const fresh = preview();
            fresh.changes[0] = { ...fresh.changes[0], current: { start_date: current.start_date, due_date: current.due_date },
                proposed: { start_date: task.start_date, due_date: nextDates.dueDate }, expected_updated_at: current.updated_at };
            if (!includeSource) fresh.changes = fresh.changes.filter(change => change.task_id !== task.id);
            return fresh;
        }
        const data = body as { changes: { task_id: number; start_date: string | null; due_date: string | null; expected_updated_at: string }[] };
        const source = data.changes.find(change => change.task_id === task.id);
        if (source && source.expected_updated_at !== current.updated_at) throw new ApiError(409, conflict);
        if (source) Object.assign(current, { start_date: source.start_date, due_date: source.due_date });
        return { applied_count: data.changes.length, task_ids: data.changes.map(change => change.task_id) };
    });
    const { saveDates, client } = setup({ linked: true });
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await userEvent.click(screen.getByRole("button", { name: "Изменить даты" }));
    // Другой участник переносит начало после открытия окна, до нового расчёта каскада.
    Object.assign(current, { start_date: "2026-11-17", updated_at: "2026-11-16T14:00:00Z" });
    fireEvent.change(screen.getByLabelText("Завершение"), { target: { value: nextDates.dueDate } });
    await screen.findByRole("checkbox", { name: "Сдвинуть зависимые задачи (1)" });
    await userEvent.type(screen.getByRole("textbox", { name: /Причина/ }), "Согласовали перенос завершения");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить и сдвинуть" }));
    await screen.findByText(conflict);
    expect(post).toHaveBeenLastCalledWith("/api/v1/projects/1/calendar/scenarios/apply", {
        date_change_reason: "Согласовали перенос завершения",
        changes: [
            { task_id: task.id, start_date: task.start_date, due_date: nextDates.dueDate, expected_updated_at: task.updated_at },
            { task_id: 23, ...preview().changes[1].proposed, expected_updated_at: preview().changes[1].expected_updated_at },
        ],
    });
    expect(current.start_date).toBe("2026-11-17");
    expect(current.due_date).toBe(task.due_date);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["projects", 1] });
    for (const taskId of [22, 23]) {
        expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.task(taskId) });
        expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.taskActivity(taskId) });
    }
    expect(screen.getByRole("dialog", { name: "Изменить сроки" })).toBeTruthy();
    expect((screen.getByLabelText("Начало") as HTMLInputElement).value).toBe(task.start_date);
    expect((screen.getByLabelText("Завершение") as HTMLInputElement).value).toBe(nextDates.dueDate);
    expect((screen.getByRole("textbox", { name: /Причина/ }) as HTMLTextAreaElement).value).toBe("Согласовали перенос завершения");
    expect(saveDates).not.toHaveBeenCalled();
});

it("выбор только одной задачи сохраняет её с той же причиной без применения каскада", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue(preview());
    const { saveDates } = setup({ linked: true });
    await userEvent.click(screen.getByRole("button", { name: "Перенести" }));
    const shift = await screen.findByRole("checkbox", { name: "Сдвинуть зависимые задачи (1)" });
    await userEvent.click(shift);
    await userEvent.type(screen.getByRole("textbox", { name: /Причина/ }), "Изменение приоритета");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить сроки" }));
    expect(saveDates).toHaveBeenCalledExactlyOnceWith(22, { ...nextDates, dateChangeReason: "Изменение приоритета" }, 7);
    expect(post.mock.calls.filter(([path]) => path.endsWith("/apply"))).toHaveLength(0);
});

it("поздний расчёт после правки или отмены не возвращает устаревшие последствия", async () => {
    let first: (value: ScenarioPreview) => void = () => undefined;
    let second: (value: ScenarioPreview) => void = () => undefined;
    vi.spyOn(api, "post").mockImplementationOnce(() => new Promise(resolve => { first = resolve; }))
        .mockImplementationOnce(() => new Promise(resolve => { second = resolve; }));
    const { saveDates } = setup({ linked: true });
    await userEvent.click(screen.getByRole("button", { name: "Перенести" }));
    fireEvent.change(screen.getByLabelText("Завершение"), { target: { value: "2026-11-24" } });
    await act(async () => { first(preview()); });
    expect(screen.queryByRole("checkbox")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Отмена" }));
    await act(async () => { second(preview("2026-11-24")); });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(saveDates).not.toHaveBeenCalled();
});

it("ошибка версии сохраняет даты и причину для повторной попытки", async () => {
    const saveDates = vi.fn().mockRejectedValue(new Error("Задача уже изменена"));
    setup({ saveDates });
    await userEvent.click(screen.getByRole("button", { name: "Перенести" }));
    await userEvent.type(screen.getByRole("textbox", { name: /Причина/ }), "Пересмотр плана");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить сроки" }));
    await screen.findByText("Задача уже изменена");
    expect((screen.getByLabelText("Завершение") as HTMLInputElement).value).toBe(nextDates.dueDate);
    expect((screen.getByRole("textbox", { name: /Причина/ }) as HTMLTextAreaElement).value).toBe("Пересмотр плана");
});

it("фиксация baseline не меняет даты и не требует объяснения переноса", async () => {
    const { saveDates, fixTaskBaseline } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Изменить даты" }));
    await userEvent.click(screen.getByRole("button", { name: "Зафиксировать baseline" }));
    expect(fixTaskBaseline).toHaveBeenCalledExactlyOnceWith(22);
    expect(saveDates).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});
