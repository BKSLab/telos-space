// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import type { ProjectMember } from "@/lib/types";
import { RiskForm } from "./RiskForm";

vi.mock("@/components/risks/RiskFieldAssistant", () => ({ RiskFieldAssistant: () => null }));
const stamp = "2026-11-15T10:00:00Z";
const people: ProjectMember[] = [{ id: 901, project_id: 1, role: "MEMBER", created_at: stamp, user: { id: 7, username: "oleg", first_name: "Олег", last_name: "Петров", middle_name: null, has_avatar: true } }];
const clients: QueryClient[] = [];
function setup(ownerId: number | null = null, saving = false, memberStatus = 200) {
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    const state = { memberStatus };
    vi.spyOn(globalThis, "fetch").mockImplementation(async input => {
        const path = new URL(String(input), "http://localhost").pathname;
        const isMembers = path.endsWith("/members");
        const status = isMembers ? state.memberStatus : 200;
        return new Response(JSON.stringify(status === 200 ? isMembers ? people : [] : { detail: "Команда временно недоступна" }), { status, headers: { "content-type": "application/json" } });
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    clients.push(client);
    const save = vi.fn();
    const tree = (busy: boolean) => <QueryClientProvider client={client}><RiskForm projectId={1} formId="risk-form" isSaving={busy} initial={{ title: "Задержка поставки", description: "Поставщик задерживает результат", owner_user_id: ownerId }} onSave={save} /><button type="submit" form="risk-form">Сохранить риск</button></QueryClientProvider>;
    const view = render(tree(saving));
    return { ...view, state, save, setSaving: (busy: boolean) => view.rerender(tree(busy)) };
}
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("выбирает владельца с фото и сохраняет user.id, затем позволяет снять назначение", async () => {
    const { save } = setup();
    const owner = screen.getByRole("button", { name: "Ответственный" });
    await waitFor(() => expect((owner as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(owner);
    const dialog = screen.getByRole("dialog", { name: "Ответственный" });
    const option = within(dialog).getByRole("option", { name: "Петров Олег" });
    expect(option.querySelector('img[src*="/users/7/avatar"]')).toBeTruthy();
    fireEvent.click(option);
    fireEvent.click(screen.getByRole("button", { name: "Сохранить риск" }));
    expect(save).toHaveBeenLastCalledWith(expect.objectContaining({ owner_user_id: 7 }));
    fireEvent.click(owner);
    fireEvent.click(within(screen.getByRole("dialog", { name: "Ответственный" })).getByRole("option", { name: "Не назначен" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить риск" }));
    expect(save).toHaveBeenLastCalledWith(expect.objectContaining({ owner_user_id: null }));
});

it("сохраняет неизвестного владельца и не добавляет его в доступные варианты", async () => {
    const { save } = setup(77);
    const owner = screen.getByRole("button", { name: "Ответственный" });
    await waitFor(() => expect((owner as HTMLButtonElement).disabled).toBe(false));
    expect(owner.textContent).toContain("Участник #77");
    fireEvent.click(screen.getByRole("button", { name: "Сохранить риск" }));
    expect(save).toHaveBeenCalledWith(expect.objectContaining({ owner_user_id: 77 }));
    fireEvent.click(owner);
    expect(screen.queryByRole("option", { name: "Участник #77" })).toBeNull();
});

it("ошибка загрузки команды блокирует выбор и допускает повтор, saving блокирует запись", async () => {
    const { state, save, container, setSaving } = setup(7, false, 503);
    await screen.findByText("Не удалось загрузить команду");
    const owner = screen.getByRole("button", { name: "Ответственный" }) as HTMLButtonElement;
    expect(owner.disabled).toBe(true);
    state.memberStatus = 200;
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    await waitFor(() => expect(owner.textContent).toContain("Петров Олег"));
    expect(owner.disabled).toBe(false);
    setSaving(true);
    expect(owner.disabled).toBe(true);
    fireEvent.submit(container.querySelector("form")!);
    expect(save).not.toHaveBeenCalled();
});
