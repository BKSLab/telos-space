// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { ProjectMember } from "@/lib/types";
import { ProjectTeamPage } from "./ProjectTeamPage";

vi.mock("@/lib/useProjectOutlet", () => ({ useProjectOutlet: () => ({ id: 1, key: "DEMO" }) }));
vi.mock("@/lib/useAuth", () => ({ useCurrentUser: () => ({ data: { id: 1 } }) }));
const stamp = "2026-11-15T10:00:00Z";
const members: ProjectMember[] = [
    { id: 901, project_id: 1, role: "OWNER", created_at: stamp, user: { id: 1, username: "anna", first_name: "Анна", last_name: "Иванова", middle_name: null, has_avatar: true } },
    { id: 902, project_id: 1, role: "MEMBER", created_at: stamp, user: { id: 2, username: "oleg", first_name: "Олег", last_name: "Петров", middle_name: null, has_avatar: false } },
];
const clients: QueryClient[] = [];
function setup() {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    clients.push(client);
    vi.spyOn(api, "get").mockResolvedValue(members);
    return render(<QueryClientProvider client={client}><MemoryRouter><ProjectTeamPage /></MemoryRouter></QueryClientProvider>);
}
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); });

it("показывает общую фотографию пользователя, ФИО, логин и права владельца", async () => {
    const { container } = setup();
    await screen.findByText("Иванова Анна");
    expect(container.querySelector('img[src*="/users/1/avatar"]')).toBeTruthy();
    expect(screen.getByText("@anna")).toBeTruthy();
    expect(screen.getByText("Владелец")).toBeTruthy();
    expect(screen.getByText("Петров Олег")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Удалить Иванова Анна из команды" })).toBeNull();
    expect(screen.getByRole("button", { name: "Удалить Петров Олег из команды" })).toBeTruthy();
});

it("удаляет участника после подтверждения по user.id, сохраняя фото и имя в списке", async () => {
    const remove = vi.spyOn(api, "delete").mockResolvedValue(undefined);
    setup();
    fireEvent.click(await screen.findByRole("button", { name: "Удалить Петров Олег из команды" }));
    const dialog = await screen.findByRole("dialog", { name: "Удалить участника?" });
    expect(dialog.textContent).toContain("Петров Олег потеряет доступ");
    expect(remove).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Удалить из команды" }));
    await waitFor(() => expect(remove).toHaveBeenCalledExactlyOnceWith("/api/v1/projects/1/members/2"));
});
