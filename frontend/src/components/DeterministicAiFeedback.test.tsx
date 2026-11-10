// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { api, endpoints, queryKeys } from "@/lib/api";
import type { AnalyticsReport } from "@/lib/types";
import type { TaskChecklist } from "@/lib/checklists";
import { riskInput } from "@/lib/risks";
import { AiOperationStatus } from "@/components/ui/AiOperationStatus";
import { TaskChecklistField } from "@/components/tasks/TaskChecklistField";
import { CreateTaskDialog } from "@/components/tasks/CreateTaskDialog";
import { RiskFieldAssistant } from "@/components/risks/RiskFieldAssistant";
import { RiskSuggestions } from "@/components/risks/RiskSuggestions";
import { ProjectSummaryPanel } from "@/components/overview/ProjectSummaryPanel";

const clients: QueryClient[] = [];
function mount(node: ReactNode, seed?: (client: QueryClient) => void) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity }, mutations: { retry: false } } });
    clients.push(client);
    seed?.(client);
    return render(<QueryClientProvider client={client}><MemoryRouter>{node}</MemoryRouter></QueryClientProvider>);
}
function deferred() {
    let resolve!: (value: unknown) => void;
    let reject!: (reason: Error) => void;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}
async function pendingStatus(message: string) {
    const status = (await screen.findByText(message)).closest('[role="status"]')!;
    expect(status.getAttribute("aria-live")).toBe("polite");
    expect(status.getAttribute("aria-atomic")).toBe("true");
    return status;
}
afterEach(() => {
    cleanup();
    clients.splice(0).forEach(client => client.clear());
    vi.restoreAllMocks();
});

const currentChecklist: TaskChecklist = { title: "Приёмка", items: [{ id: "item-1", text: "Сверить контракт", is_completed: false }] };
const suggestion = { checklist: { title: "Предложение", items: [{ id: "item-2", text: "Проверить ответ", is_completed: false }] }, warnings: [] };
const riskSuggestion = { title: "Задержка контракта", description: "Поставщик задерживает API", probability: "HIGH", impact: "MEDIUM", response_strategy: "MITIGATE", mitigation_plan: "Согласовать контракт", response_plan: "Запустить резервный API", task_id: null, evidence: ["Срок поставщика"] };
const report: AnalyticsReport = {
    id: 1, scope: "PROJECT", project_id: 1, project_key: "DEMO", project_name: "Проект",
    created_at: "2026-01-01T10:00:00Z", created_by: "Автор", llm_model: "test", duration_ms: 1,
    headline: "Сохранённый разбор", health: "STABLE", health_note: "Работы идут по плану.", findings: [], progress: [], recommendations: [],
    signals: { total_tasks: 1, done_tasks: 0, overdue_tasks: 0, due_soon_tasks: 0, no_due_date_tasks: 0, unassigned_tasks: 0, stale_tasks: 0, blocked_tasks: 0, milestones_at_risk: 0, unplaced_tasks: 0 },
    context: { projects: 1, tasks_total: 1, tasks_included: 1, comments_included: 0, documents_included: 0, stickers_included: 0, milestones_included: 0, activity_included: 0, truncated: false, omitted: [], wbs_nodes_included: 0 },
};

it("сообщает действие и дополнительное пояснение текстом", () => {
    render(<AiOperationStatus message="Арис готовит предложение…" description="Результат появится здесь." />);
    const status = screen.getByRole("status");
    expect(status.textContent).toContain("Арис готовит предложение…");
    expect(status.textContent).toContain("Результат появится здесь.");
});

it.each(["success", "error"])("чек-лист показывает ожидание до ответа (%s) и сохраняет текущие пункты", async outcome => {
    const response = deferred();
    const post = vi.spyOn(api, "postForm").mockReturnValue(response.promise);
    const change = vi.fn();
    mount(<TaskChecklistField projectId={1} taskId={7} title="Проверить API" description="Черновик задачи"
        value={currentChecklist} onChange={change} editing={false} />);
    expect(screen.queryByText("Арис готовит чек-лист задачи…")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Сформировать чек-лист" }));
    await pendingStatus("Арис готовит чек-лист задачи…");
    const button = screen.getByRole("button", { name: "Формирование…" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(post).toHaveBeenCalledOnce();
    expect((screen.getByRole("checkbox", { name: "Сверить контракт" }) as HTMLInputElement).disabled).toBe(true);
    await act(async () => {
        if (outcome === "success") response.resolve(suggestion);
        else response.reject(new Error("Провайдер недоступен"));
    });
    await waitFor(() => expect(screen.queryByText("Арис готовит чек-лист задачи…")).toBeNull());
    expect((screen.getByRole("checkbox", { name: "Сверить контракт" }) as HTMLInputElement).disabled).toBe(false);
    if (outcome === "success") expect(screen.getByRole("region", { name: "Предложение AI" })).toBeTruthy();
    else expect(screen.getByRole("alert").textContent).toContain("Провайдер недоступен");
    expect(change).not.toHaveBeenCalled();
});

it("переформулирование снимает ожидание при успехе и ошибке, сохраняя последний текст", async () => {
    const first = deferred();
    const second = deferred();
    const post = vi.spyOn(api, "postForm").mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    mount(<CreateTaskDialog projectId={1} stages={[]} isOpen onClose={vi.fn()} />, client => {
        client.setQueryData(queryKeys.currentUser, { id: 1, username: "user" });
        client.setQueryData(queryKeys.projectMembers(1), []);
        client.setQueryData(queryKeys.documents(1), []);
    });
    fireEvent.change(screen.getByLabelText("Название", { exact: true }), { target: { value: "Контракт API" } });
    const description = screen.getByLabelText(/Описание/) as HTMLTextAreaElement;
    fireEvent.change(description, { target: { value: "Исходный текст" } });
    fireEvent.click(screen.getByRole("button", { name: "Переформулировать" }));
    await pendingStatus("Арис переформулирует описание задачи…");
    expect(description.disabled).toBe(true);
    expect(description.value).toBe("Исходный текст");
    expect((screen.getByRole("button", { name: "Переформулируем…" }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => first.resolve({ description_md: "Уточнённый текст" }));
    await waitFor(() => expect(screen.queryByText("Арис переформулирует описание задачи…")).toBeNull());
    expect(description.value).toBe("Уточнённый текст");
    fireEvent.click(screen.getByRole("button", { name: "Переформулировать" }));
    await pendingStatus("Арис переформулирует описание задачи…");
    await act(async () => second.reject(new Error("Провайдер недоступен")));
    await waitFor(() => expect(description.disabled).toBe(false));
    expect(screen.queryByText("Арис переформулирует описание задачи…")).toBeNull();
    expect(description.value).toBe("Уточнённый текст");
    expect(screen.getByRole("alert").textContent).toContain("Исходный текст не изменён");
    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.calls.every(([path]) => path === endpoints.taskRephrase(1))).toBe(true);
});

it.each([
    ["description", "Арис формулирует описание риска…"],
    ["mitigation_plan", "Арис готовит план митигации…"],
    ["response_plan", "Арис готовит план реагирования…"],
] as const)("формулировка поля %s ожидает ответа и сохраняет предложение при следующей ошибке", async (field, message) => {
    const first = deferred();
    const second = deferred();
    const post = vi.spyOn(api, "post").mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const accept = vi.fn();
    mount(<RiskFieldAssistant projectId={1} field={field} draft={riskInput({ description: "Мой черновик" })} onAccept={accept} />);
    fireEvent.click(screen.getByRole("button", { name: "Помочь сформулировать" }));
    await pendingStatus(message);
    const button = screen.getByRole("button", { name: "Формулируем…" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(button);
    expect(post).toHaveBeenCalledOnce();
    await act(async () => first.resolve({ field, text: "Готовое предложение", warnings: [] }));
    await screen.findByText("Готовое предложение");
    expect(screen.queryByText(message)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Помочь сформулировать" }));
    await pendingStatus(message);
    expect(screen.getByText("Готовое предложение")).toBeTruthy();
    await act(async () => second.reject(new Error("Провайдер недоступен")));
    await waitFor(() => expect(screen.queryByText(message)).toBeNull());
    expect(screen.getByText("Готовое предложение")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("Провайдер недоступен");
    expect(accept).not.toHaveBeenCalled();
    expect(post.mock.calls[0]).toEqual([endpoints.projectRiskFieldSuggestion(1), expect.objectContaining({ field, description: "Мой черновик" })]);
});

it("предложения рисков используют один статус и сохраняются при ошибке повторного запроса", async () => {
    const first = deferred();
    const second = deferred();
    const post = vi.spyOn(api, "post").mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    mount(<RiskSuggestions projectId={1} />);
    fireEvent.click(screen.getByRole("button", { name: "Предложить риски" }));
    await pendingStatus("Арис изучает проект и предлагает риски…");
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect((screen.getByRole("button", { name: "Изучаю проект…" }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => first.resolve({ suggestions: [riskSuggestion] }));
    await screen.findByText(riskSuggestion.title);
    expect(screen.queryByText("Арис изучает проект и предлагает риски…")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Предложить риски" }));
    await pendingStatus("Арис изучает проект и предлагает риски…");
    expect(screen.getByText(riskSuggestion.title)).toBeTruthy();
    await act(async () => second.reject(new Error("Провайдер недоступен")));
    await waitFor(() => expect(screen.queryByText("Арис изучает проект и предлагает риски…")).toBeNull());
    expect(screen.getByText(riskSuggestion.title)).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("Провайдер недоступен");
    expect(post).toHaveBeenCalledTimes(2);
});

it.each([1, null])("разбор (%s) сохраняет прошлый результат во время ожидания и после ошибки", async projectId => {
    const first = deferred();
    const second = deferred();
    const post = vi.spyOn(api, "post").mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const message = projectId ? "Арис готовит разбор проекта…" : "Арис готовит разбор портфеля…";
    mount(<ProjectSummaryPanel projectId={projectId} onOpenTask={vi.fn()} />, client => {
        client.setQueryData(queryKeys.dashboardAnalytics(projectId), { ...report, project_id: projectId, scope: projectId ? "PROJECT" : "PORTFOLIO" });
    });
    fireEvent.click(screen.getByRole("button", { name: "Обновить разбор" }));
    await pendingStatus(message);
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(screen.getByText(report.headline)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Разбираю…" }) as HTMLButtonElement).disabled).toBe(true);
    await act(async () => first.resolve({ ...report, id: 2, headline: "Новый разбор" }));
    await screen.findByText("Новый разбор");
    expect(screen.queryByText(message)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Обновить разбор" }));
    await pendingStatus(message);
    await act(async () => second.reject(new Error("Провайдер недоступен")));
    await waitFor(() => expect(screen.queryByText(message)).toBeNull());
    expect(screen.getByText("Новый разбор")).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("Провайдер недоступен");
    expect(post).toHaveBeenCalledTimes(2);
    expect(post.mock.calls[0]).toEqual([endpoints.dashboardAnalytics(), { project_id: projectId }]);
});
