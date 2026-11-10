// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ProfilePage } from "./ProfilePage";
import { api, authEndpoints, queryKeys } from "@/lib/api";
import { personalApiKeyQueryKeys } from "@/lib/apiKeys";
import type { User } from "@/lib/types";

const user: User = { id: 1, username: "tester", first_name: "Тест", last_name: "Тестов", middle_name: null, has_avatar: false, email: null, phone: null, telegram: null, created_at: "2026-11-04T06:00:00Z" };
const clients: QueryClient[] = [];
function setup(path = "/profile") {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    clients.push(client);
    client.setQueryData(queryKeys.currentUser, user);
    client.setQueryData(personalApiKeyQueryKeys.list, []);
    client.setQueryData(personalApiKeyQueryKeys.scopes, []);
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><ProfilePage /></MemoryRouter></QueryClientProvider>);
}
beforeEach(() => {
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => setTimeout(() => callback(0), 0));
    vi.stubGlobal("cancelAnimationFrame", clearTimeout);
});
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("разделяет данные, безопасность и ключи и сохраняет черновик между вкладками", async () => {
    setup();
    await userEvent.clear(screen.getByLabelText("Имя"));
    await userEvent.type(screen.getByLabelText("Имя"), "Новое имя");
    const tabs = within(screen.getByRole("navigation", { name: "Разделы профиля" }));
    await userEvent.click(tabs.getByRole("link", { name: "Ключи доступа" }));
    expect(screen.getByRole("region", { name: "API-ключи" })).toBeTruthy();
    expect(screen.queryByLabelText("Имя")).toBeNull();
    expect(screen.queryByLabelText("Текущий пароль")).toBeNull();
    await userEvent.click(tabs.getByRole("link", { name: "Личные данные" }));
    expect((screen.getByLabelText("Имя") as HTMLInputElement).value).toBe("Новое имя");
});
it("сохраняет личные данные тем же запросом без полей владельца", async () => {
    const patch = vi.spyOn(api, "patch").mockResolvedValue({ ...user, first_name: "Анна" });
    setup();
    await userEvent.clear(screen.getByLabelText("Имя"));
    await userEvent.type(screen.getByLabelText("Имя"), " Анна ");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith(authEndpoints.profile(), { first_name: "Анна", last_name: "Тестов", middle_name: null, email: null, phone: null, telegram: null }));
});
it("проверяет повтор пароля и сохраняет прежний контракт смены пароля", async () => {
    const post = vi.spyOn(api, "post").mockResolvedValue(undefined);
    setup("/profile?tab=security");
    await userEvent.type(screen.getByLabelText("Текущий пароль"), "OldTestPassword123!");
    await userEvent.type(screen.getByLabelText("Новый пароль"), "NewTestPassword123!");
    await userEvent.type(screen.getByLabelText("Повторите пароль"), "DifferentPassword123!");
    expect((screen.getByRole("button", { name: "Сменить пароль" }) as HTMLButtonElement).disabled).toBe(true);
    await userEvent.clear(screen.getByLabelText("Повторите пароль"));
    await userEvent.type(screen.getByLabelText("Повторите пароль"), "NewTestPassword123!");
    await userEvent.click(screen.getByRole("button", { name: "Сменить пароль" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith(authEndpoints.password(), { current_password: "OldTestPassword123!", password: "NewTestPassword123!", password_confirm: "NewTestPassword123!" }));
    await waitFor(() => expect((screen.getByLabelText("Новый пароль") as HTMLInputElement).value).toBe(""));
});
it("загружает READ/WRITE-токены только по раскрытию блока", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue([]);
    setup("/profile?tab=keys");
    expect(get).not.toHaveBeenCalled();
    await userEvent.click(screen.getByText("READ/WRITE-токены", { selector: "summary" }));
    expect(await screen.findByText("READ/WRITE-токенов нет")).toBeTruthy();
    expect(get).toHaveBeenCalledWith(authEndpoints.apiTokens());
});
