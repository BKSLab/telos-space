// @vitest-environment jsdom
import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { api, endpoints } from "@/lib/api";
import type { UserSummary } from "@/lib/types";
import { LocalUserSelect } from "./LocalUserSelect";

const saved: UserSummary = { id: 7, username: "anna", first_name: "Анна", last_name: "Иванова", middle_name: null, has_avatar: true };
const found: UserSummary = { ...saved, id: 9, username: "oleg", first_name: "Олег", last_name: "Петров" };
const clients: QueryClient[] = [];

function setup(multiple = false) {
    vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
    const get = vi.spyOn(api, "get").mockResolvedValue([found]);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    clients.push(client);
    const change = vi.fn();
    function Example() {
        const [selected, setSelected] = useState([saved]);
        return <LocalUserSelect label="Участники" selected={selected} multiple={multiple}
            onChange={next => { change(next); setSelected(next); }} />;
    }
    render(<QueryClientProvider client={client}><Example /></QueryClientProvider>);
    return { get, change, user: userEvent.setup() };
}

afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("сохраняет глобальный поиск и выбранного пользователя вне результатов, отображая фото в обоих местах", async () => {
    const { get, change, user } = setup();
    const trigger = screen.getByRole("button", { name: "Участники" });
    expect(trigger.querySelector('img[src*="/users/7/avatar"]')).toBeTruthy();
    await user.click(trigger);
    expect(await screen.findByRole("option", { name: "Петров Олег" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "Иванова Анна" })).toBeTruthy();
    fireEvent.change(screen.getByRole("textbox", { name: "Поиск: Участники" }), { target: { value: "Олег +" } });
    await waitFor(() => expect(get).toHaveBeenCalledWith(endpoints.users("Олег +")));
    const option = await screen.findByRole("option", { name: "Петров Олег" });
    expect(option.querySelector('img[src*="/users/9/avatar"]')).toBeTruthy();
    await user.click(option);
    expect(change).toHaveBeenLastCalledWith([found]);
    expect(screen.queryByRole("dialog", { name: "Участники" })).toBeNull();
    expect(trigger.querySelector('img[src*="/users/9/avatar"]')).toBeTruthy();
    await user.click(trigger);
    await user.click(screen.getByRole("option", { name: "Не назначен" }));
    expect(change).toHaveBeenLastCalledWith([]);
});

it("в множественном режиме сохраняет UserSummary и не закрывает выбор после добавления", async () => {
    const { change, user } = setup(true);
    await user.click(screen.getByRole("button", { name: "Участники" }));
    await user.click(await screen.findByRole("option", { name: "Петров Олег" }));
    expect(change).toHaveBeenLastCalledWith([saved, found]);
    expect(screen.getByRole("dialog", { name: "Участники" })).toBeTruthy();
    await user.click(screen.getByRole("option", { name: "Иванова Анна" }));
    expect(change).toHaveBeenLastCalledWith([found]);
});

it("показывает сбой каталога, сохраняя существующий выбор", async () => {
    const { get, user } = setup();
    await user.click(screen.getByRole("button", { name: "Участники" }));
    await screen.findByRole("option", { name: "Петров Олег" });
    get.mockRejectedValue(new Error("network"));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "другой запрос" } });
    expect((await screen.findByRole("alert")).textContent).toBe("Не удалось загрузить пользователей.");
    expect(screen.getByRole("option", { name: "Иванова Анна" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("button", { name: "Участники" }).textContent).toContain("Иванова Анна");
});
