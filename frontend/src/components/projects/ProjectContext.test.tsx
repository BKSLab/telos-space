// @vitest-environment jsdom
import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { toProjectFormValues } from "@/lib/projectForm";
import type { Project, ProjectContextHistoryPage } from "@/lib/types";
import { ProjectContextGaps } from "./ProjectContextGaps";
import { ProjectContextHistory } from "./ProjectContextHistory";
import { ProjectForm } from "./ProjectForm";

const project: Project = {
    id: 7, key: "TEST", name: "Проект", owner_id: 1,
    description_md: "Прежнее описание", context_revision: 0,
    status: "ACTIVE", color: "#58a6ff", icon: null, order_index: 0,
    start_date: null, due_date: null,
    created_at: "2026-11-01T00:00:00Z", updated_at: "2026-11-16T00:00:00Z",
};
const clients: QueryClient[] = [];
function provider(children: React.ReactNode) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    clients.push(client);
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); });

function DraftForm() {
    const [values, setValues] = useState(toProjectFormValues(project));
    return <ProjectForm values={values} onChange={setValues} />;
}

it("оставляет неизвестные поля пустыми и позволяет заполнить контекст без подмены прежнего описания", () => {
    render(provider(<DraftForm />));
    const goal = screen.getByLabelText("Цель") as HTMLTextAreaElement;
    expect(goal.value).toBe("");
    expect((screen.getByLabelText("Дополнительно") as HTMLTextAreaElement).value).toBe("Прежнее описание");
    fireEvent.click(screen.getByText("Контекст проекта"));
    const constraints = screen.getByLabelText("Ограничения") as HTMLTextAreaElement;
    expect(constraints.value).toBe("");
    fireEvent.change(constraints, { target: { value: "Данные хранятся локально" } });
    expect(constraints.value).toBe("Данные хранятся локально");
    expect(goal.value).toBe("");
});

it("показывает пробелы, не выводя цель из старого текста", () => {
    render(<ProjectContextGaps project={{ ...project, description_md: "## Цель\n\nСтарый текст" }} />);
    expect(screen.getByText(/Пока не определено:/).textContent).toContain("Цель");
    expect(screen.getByText(/Пока не определено:/).textContent).not.toContain("Дополнительно");
});

it("читает страницы истории и отличает известного автора от исходного снимка", async () => {
    const page: ProjectContextHistoryPage = {
        items: [{ revision: 3, changed_by_user_id: 1, changed_by_name: "Анна Иванова",
            created_at: "2026-11-16T10:00:00Z", is_initial: false, changed_fields: ["goal", "constraints"] }],
        total: 21, offset: 0, limit: 20, has_more: true, next_offset: 20,
    };
    const baseline: ProjectContextHistoryPage = {
        ...page, items: [{ revision: 0, changed_by_user_id: null, changed_by_name: null,
            created_at: "2026-11-16T09:00:00Z", is_initial: true, changed_fields: ["description_md"] }],
        offset: 20, has_more: false, next_offset: null,
    };
    const get = vi.spyOn(api, "get").mockResolvedValueOnce(page).mockResolvedValueOnce(baseline);
    render(provider(<ProjectContextHistory projectId={7} />));
    await screen.findByText("Версия 3");
    expect(screen.getByText("Анна Иванова")).toBeTruthy();
    expect(screen.getByText("Цель, Ограничения")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Раньше" }));
    await screen.findByText("Версия 0 · Исходное состояние");
    expect(screen.getByText(/Автор прежних правок неизвестен/).textContent).toContain("дата фиксации");
    await waitFor(() => expect(get).toHaveBeenLastCalledWith("/api/v1/projects/7/context-history?limit=20&offset=20"));
    expect((screen.getByRole("button", { name: "Раньше" }) as HTMLButtonElement).disabled).toBe(true);
});

it("при недоступной истории показывает ошибку и действие повтора", async () => {
    vi.spyOn(api, "get").mockRejectedValue(new Error("Нет доступа к проекту"));
    render(provider(<ProjectContextHistory projectId={7} />));
    await screen.findByText("Нет доступа к проекту");
    expect(screen.getByRole("button", { name: "Повторить" })).toBeTruthy();
    expect(screen.queryByText(/Версия/)).toBeNull();
});
