// @vitest-environment jsdom
import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ProjectMember } from "@/lib/types";
import { TaskPeopleFields } from "./TaskPeopleFields";

const members: ProjectMember[] = [
    { id: 901, project_id: 1, role: "OWNER", created_at: "2026-11-15", user: { id: 7, username: "anna", first_name: "Анна", last_name: "Иванова", middle_name: null, has_avatar: true } },
    { id: 902, project_id: 1, role: "MEMBER", created_at: "2026-11-15", user: { id: 9, username: "oleg", first_name: "Олег", last_name: "Петров", middle_name: null, has_avatar: false } },
];
const callbacks = () => ({ onExecutorChange: vi.fn(), onReporterChange: vi.fn(), onObserversChange: vi.fn() });
beforeEach(() => { vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} }); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("предлагает только переданную команду с фото и сохраняет user.id, а не membership ID", async () => {
    const user = userEvent.setup();
    const handlers = callbacks();
    const fetch = vi.spyOn(globalThis, "fetch");
    render(<TaskPeopleFields members={members} executorId={7} reporterId={null} observerIds={[]} {...handlers} />);
    const executor = screen.getByRole("button", { name: "Исполнитель" });
    expect(executor.querySelector('img[src*="/users/7/avatar"]')).toBeTruthy();
    expect(screen.getByLabelText("Исполнитель")).toBe(executor);
    await user.click(executor);
    expect(screen.getAllByRole("option")).toHaveLength(3);
    expect(screen.getByText("@anna · владелец")).toBeTruthy();
    await user.click(screen.getByRole("option", { name: "Петров Олег" }));
    expect(handlers.onExecutorChange).toHaveBeenCalledExactlyOnceWith(9);
    expect(fetch).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Постановщик" }));
    await user.click(screen.getByRole("option", { name: "Иванова Анна" }));
    expect(handlers.onReporterChange).toHaveBeenCalledExactlyOnceWith(7);
});

it("сохраняет запрет смены постановщика и assignee snapshot", () => {
    render(<TaskPeopleFields members={members} executorId={null} reporterId={7} observerIds={[]} canChangeReporter={false}
        assigneeSnapshot="Внешний специалист" {...callbacks()} />);
    expect((screen.getByRole("button", { name: "Постановщик" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Изменяет только владелец")).toBeTruthy();
    expect(screen.getByText("Ранее указано: Внешний специалист")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Постановщик" }));
    expect(screen.queryByRole("dialog")).toBeNull();
});

it("не теряет неизвестные ID наблюдателей при переключении команды, но очищает их явной очисткой", async () => {
    const user = userEvent.setup();
    const handlers = callbacks();
    function Example() {
        const [observerIds, setObserverIds] = useState([77, 7]);
        return <TaskPeopleFields members={members} executorId={null} reporterId={7} observerIds={observerIds}
            {...handlers} onObserversChange={next => { handlers.onObserversChange(next); setObserverIds(next); }} />;
    }
    render(<Example />);
    await user.click(screen.getByRole("button", { name: "Выбор наблюдателей" }));
    expect(screen.queryByRole("option", { name: "Участник #77" })).toBeNull();
    await user.click(screen.getByRole("option", { name: "Петров Олег" }));
    expect(handlers.onObserversChange).toHaveBeenLastCalledWith([77, 7, 9]);
    await user.click(screen.getByRole("option", { name: "Иванова Анна" }));
    expect(handlers.onObserversChange).toHaveBeenLastCalledWith([77, 9]);
    await user.click(screen.getByRole("option", { name: "Петров Олег" }));
    expect(handlers.onObserversChange).toHaveBeenLastCalledWith([77]);
    expect(screen.getByRole("button", { name: "Выбор наблюдателей" }).textContent).toContain("Участник #77");
    await user.click(screen.getByRole("button", { name: "Очистить выбор: Выбор наблюдателей" }));
    expect(handlers.onObserversChange).toHaveBeenLastCalledWith([]);
});

it("показывает неизвестного исполнителя вне вариантов, сохраняя запрет всех действий во время записи", () => {
    const handlers = callbacks();
    const tree = (disabled: boolean) => <TaskPeopleFields members={members} executorId={77} reporterId={null} observerIds={[]}
        assigneeSnapshot="Прежний исполнитель" disabled={disabled} {...handlers} />;
    const view = render(tree(false));
    const executor = screen.getByRole("button", { name: "Исполнитель" });
    expect(executor.textContent).toContain("Прежний исполнитель");
    fireEvent.click(executor);
    expect(screen.queryByRole("option", { name: "Прежний исполнитель" })).toBeNull();
    fireEvent.click(screen.getByRole("option", { name: "Не назначен" }));
    expect(handlers.onExecutorChange).toHaveBeenCalledExactlyOnceWith(null);
    view.rerender(tree(true));
    for (const label of ["Исполнитель", "Постановщик", "Выбор наблюдателей"]) {
        expect((screen.getByRole("button", { name: label }) as HTMLButtonElement).disabled).toBe(true);
    }
});
