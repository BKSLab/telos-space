// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it } from "vitest";
import { queryKeys } from "@/lib/api";
import type { Project, ProjectMember } from "@/lib/types";
import { ProjectPeopleSummary } from "./ProjectPeopleSummary";

const stamp = "2026-11-15T10:00:00Z";
const project: Project = { id: 1, owner_id: 2, key: "DEMO", name: "Проект", description_md: null, status: "ACTIVE", color: "#7299cc", icon: null, start_date: null, due_date: null, order_index: 0, created_at: stamp, updated_at: stamp };
const members: ProjectMember[] = [
    { id: 901, project_id: 1, role: "OWNER", created_at: stamp, user: { id: 1, username: "anna", first_name: "Анна", last_name: "Иванова", middle_name: null, has_avatar: true } },
    { id: 902, project_id: 1, role: "MEMBER", created_at: stamp, user: { id: 2, username: "oleg", first_name: "Олег", last_name: "Петров", middle_name: null, has_avatar: true } },
];
const clients: QueryClient[] = [];
function setup(people = members) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    clients.push(client);
    client.setQueryData(queryKeys.projectMembers(1), people);
    return render(<QueryClientProvider client={client}><MemoryRouter><ProjectPeopleSummary project={project} /></MemoryRouter></QueryClientProvider>);
}
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); });

it("показывает фото руководителя по owner_id и все фото команды в ссылке на команду", () => {
    const { container } = setup();
    const manager = container.querySelector("dt + dd");
    expect(manager?.textContent).toContain("Петров Олег");
    expect(manager?.querySelector('img[src*="/users/2/avatar"]')).toBeTruthy();
    const team = screen.getByRole("link", { name: "Открыть команду проекта" });
    expect(team.getAttribute("href")).toBe("/projects/DEMO/team");
    expect(team.querySelectorAll("img")).toHaveLength(2);
});

it("пустая команда оставляет явные состояния руководителя и ссылку", () => {
    setup([]);
    expect(screen.getByText("Не указан")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Открыть команду проекта" }).textContent).toBe("Открыть команду");
});
