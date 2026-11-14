// @vitest-environment jsdom
import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { UserSummary } from "@/lib/types";
import { UserSelect } from "./UserSelect";

const alice: UserSummary = { id: 7, username: "alice", first_name: "Анна", last_name: "Иванова", middle_name: null, has_avatar: true };
const bob: UserSummary = { ...alice, id: 8, username: "bob", first_name: "Борис", last_name: "Петров", has_avatar: false };

beforeEach(() => { vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} }); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("показывает защищённые фото в значении и вариантах, не превращает сохранённое значение в доступный вариант", async () => {
    const user = userEvent.setup();
    render(<><label htmlFor="person">Ответственный</label><UserSelect id="person" label="Ответственный" users={[bob, bob]} selected={[alice]} onChange={vi.fn()} /></>);
    const trigger = screen.getByRole("button", { name: "Ответственный" });
    expect(trigger.id).toBe("person");
    expect(trigger.querySelector('img[src*="/api/v1/users/7/avatar"]')).toBeTruthy();
    await user.click(trigger);
    const list = screen.getByRole("listbox", { name: "Ответственный" });
    expect(within(list).getAllByRole("option")).toHaveLength(2);
    expect(within(list).queryByRole("option", { name: "Иванова Анна" })).toBeNull();
    expect(within(list).getByRole("option", { name: "Петров Борис" }).querySelector('[aria-hidden="true"]')).toBeTruthy();
    expect(trigger.querySelector("button")).toBeNull();
});

it("позволяет выбрать с клавиатуры и возвращает фокус на именованный trigger", async () => {
    const user = userEvent.setup();
    const change = vi.fn();
    render(<UserSelect id="keyboard-person" label="Исполнитель" users={[alice, bob]} selected={[]} onChange={change} />);
    const trigger = screen.getByRole("button", { name: "Исполнитель" });
    await user.click(trigger);
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Поиск: Исполнитель" })));
    await user.keyboard("{ArrowDown}{End}");
    expect(document.activeElement).toBe(screen.getByRole("option", { name: "Петров Борис" }));
    await user.keyboard("{Shift>}{Tab}{/Shift}");
    expect(document.activeElement).toBe(screen.getByRole("textbox", { name: "Поиск: Исполнитель" }));
    await user.keyboard("{ArrowUp}");
    expect(document.activeElement).toBe(screen.getByRole("option", { name: "Петров Борис" }));
    await user.keyboard("{ArrowUp}{Home}{ArrowDown}{Enter}");
    expect(change).toHaveBeenCalledExactlyOnceWith([alice]);
    expect(screen.queryByRole("dialog", { name: "Исполнитель" })).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(trigger));
});

it("множественный выбор сохраняет открытый список, меняет выбранные ID и поддерживает очистку", async () => {
    const user = userEvent.setup();
    const change = vi.fn();
    function Example() {
        const [selected, setSelected] = useState([alice]);
        return <UserSelect label="Наблюдатели" users={[alice, bob]} selected={selected} multiple
            onChange={next => { change(next); setSelected(next); }} />;
    }
    render(<Example />);
    await user.click(screen.getByRole("button", { name: "Наблюдатели" }));
    expect(screen.getByRole("listbox").getAttribute("aria-multiselectable")).toBe("true");
    const aliceOption = screen.getByRole("option", { name: "Иванова Анна" });
    expect(aliceOption.querySelector('img[src*="/users/7/avatar"]')).toBeTruthy();
    await user.click(screen.getByRole("option", { name: "Петров Борис" }));
    expect(change).toHaveBeenLastCalledWith([alice, bob]);
    await user.click(aliceOption);
    expect(change).toHaveBeenLastCalledWith([bob]);
    expect(screen.getByRole("dialog", { name: "Наблюдатели" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Очистить выбор: Наблюдатели" }));
    expect(change).toHaveBeenLastCalledWith([]);
    expect(screen.getByRole("option", { name: "Петров Борис" }).getAttribute("aria-selected")).toBe("false");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Наблюдатели" })).toBeNull();
});

it("фильтрует локальные варианты по имени и логину, сохраняя выбранное значение", async () => {
    const user = userEvent.setup();
    render(<UserSelect label="Участник" users={[alice, bob]} selected={[alice]} onChange={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Участник" }));
    const search = screen.getByRole("textbox", { name: "Поиск: Участник" });
    await user.type(search, "BOB");
    expect(screen.getByRole("option", { name: "Петров Борис" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: "Иванова Анна" })).toBeNull();
    expect(screen.getByRole("button", { name: "Участник" }).textContent).toContain("Иванова Анна");
    await user.clear(search);
    await user.type(search, "анн");
    expect(screen.getByRole("option", { name: "Иванова Анна" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: "Петров Борис" })).toBeNull();
    await user.clear(search);
    await user.type(search, "нет совпадений");
    expect(screen.getByText("Пользователь не найден.")).toBeTruthy();
});

it("оставляет фильтрацию серверу при управляемом поиске и показывает loading/error", async () => {
    const search = vi.fn();
    const view = render(<UserSelect label="Каталог" users={[alice]} selected={[]} onChange={vi.fn()}
        searchValue="server-query" onSearchChange={search} loading error="Каталог недоступен" />);
    fireEvent.click(screen.getByRole("button", { name: "Каталог" }));
    expect(screen.getByRole("option", { name: "Иванова Анна" })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("Загружаем");
    expect(screen.getByRole("alert").textContent).toBe("Каталог недоступен");
    expect(screen.queryByText("Пользователь не найден.")).toBeNull();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "новый запрос" } });
    expect(search).toHaveBeenLastCalledWith("новый запрос");
    view.unmount();
});

it("показывает неизвестное назначение с фото по ID и позволяет явно снять его", async () => {
    const change = vi.fn();
    render(<UserSelect label="Владелец" users={[alice]} selected={[]} fallbackSelection={{ userId: 77, name: "Участник #77" }}
        emptyLabel="Не назначен" onChange={change} />);
    const trigger = screen.getByRole("button", { name: "Владелец" });
    expect(trigger.querySelector('img[src*="/users/77/avatar"]')).toBeTruthy();
    fireEvent.click(trigger);
    expect(screen.queryByRole("option", { name: "Участник #77" })).toBeNull();
    fireEvent.click(screen.getByRole("option", { name: "Не назначен" }));
    expect(change).toHaveBeenCalledExactlyOnceWith([]);
});

it("disabled блокирует открытие и действия уже открытого списка", () => {
    const change = vi.fn();
    const tree = (disabled: boolean) => <UserSelect label="Выбор" users={[alice]} selected={[alice]} multiple disabled={disabled} onChange={change} />;
    const view = render(tree(true));
    fireEvent.click(screen.getByRole("button", { name: "Выбор" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    view.rerender(tree(false));
    fireEvent.click(screen.getByRole("button", { name: "Выбор" }));
    view.rerender(tree(true));
    expect((screen.getByRole("textbox") as HTMLInputElement).disabled).toBe(true);
    const option = screen.getByRole("option", { name: "Иванова Анна" }) as HTMLButtonElement;
    expect(option.disabled).toBe(true);
    fireEvent.click(option);
    fireEvent.click(screen.getByRole("button", { name: "Очистить выбор: Выбор" }));
    expect(change).not.toHaveBeenCalled();
});
