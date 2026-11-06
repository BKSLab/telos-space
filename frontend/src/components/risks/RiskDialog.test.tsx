// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { queryKeys } from "@/lib/api";
import type { ProjectRisk } from "@/lib/risks";
import { useToastStore } from "@/lib/toast";
import { useUiStore } from "@/stores/ui";
import { RiskDialog } from "./RiskDialog";

vi.mock("@/lib/useCalendarView", () => ({ useMediaQuery: () => true }));

const stamp = "2026-11-06T10:00:00Z";
const risk: ProjectRisk = {
    id: 12, key: "RISK-12", project_id: 1, title: "Задержка документации",
    description: "## Причина риска\n\n**Документация** задерживается. <script>alert('unsafe')</script>",
    probability: "HIGH", impact: "HIGH", risk_level: "HIGH", status: "OPEN", response_strategy: "MITIGATE",
    mitigation_plan: "Согласовать контракт заранее", response_plan: "Использовать резервный адаптер",
    owner_user_id: null, review_date: null, task_id: 7, source: "MANUAL", created_at: stamp, updated_at: stamp,
};
const clients: QueryClient[] = [];

function setup() {
    const state = { risk: { ...risk }, readStatus: 200, patchStatus: 200, gate: null as Promise<void> | null, calls: [] as { method: string; path: string; body?: unknown }[] };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
        const path = new URL(String(input)).pathname;
        const method = init?.method ?? "GET";
        const body = init?.body ? JSON.parse(String(init.body)) : undefined;
        state.calls.push({ method, path, body });
        const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
        if (path === "/api/v1/projects/1/members" || path === "/api/v1/projects/1/tasks") return json([]);
        if (path === "/api/v1/tasks/7") return json({ id: 7, project_id: 1, key: "DEMO-7", title: "Согласовать контракт" });
        if (path === "/api/v1/projects/1/risks/12") {
            if (method === "PATCH") {
                if (state.gate) await state.gate;
                if (state.patchStatus !== 200) return json({ detail: "Не удалось сохранить изменения" }, state.patchStatus);
                state.risk = { ...state.risk, ...body };
                return json(state.risk);
            }
            if (state.readStatus !== 200) return json({ detail: "Риск недоступен" }, state.readStatus);
            return json(state.risk);
        }
        throw new Error(`Unexpected request: ${method} ${path}`);
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    clients.push(client);
    useUiStore.getState().setSelectedRisk({ projectId: 1, riskId: 12 });
    render(<QueryClientProvider client={client}><MemoryRouter><RiskDialog /></MemoryRouter></QueryClientProvider>);
    return { state, client, user: userEvent.setup() };
}

afterEach(() => {
    cleanup();
    clients.splice(0).forEach((client) => client.clear());
    useUiStore.getState().setSelectedRisk(null);
    useUiStore.getState().setSelectedTaskId(null);
    useToastStore.setState({ toasts: [] });
    vi.restoreAllMocks();
});

it("открывает общую рабочую модальную карточку с безопасным описанием", async () => {
    const { state } = setup();
    const dialog = await screen.findByRole("dialog", { name: "Риск RISK-12" });
    expect(dialog.closest(".modal-workspace")).toBeTruthy();
    expect(within(dialog).getByRole("tablist", { name: "Разделы риска" }).getAttribute("aria-orientation")).toBe("vertical");
    expect(within(dialog).getByRole("tab", { name: "Основное" }).getAttribute("aria-selected")).toBe("true");
    await within(dialog).findByRole("heading", { name: "Причина риска" });
    expect(dialog.querySelector("script")).toBeNull();
    expect(within(dialog).getByRole("button", { name: "Закрыть риск" })).toBeTruthy();
    expect(state.calls.some((call) => call.path === "/api/v1/tasks/7")).toBe(false);
});

it("разделы раскрывают планы и контроль, скрывая неактивное содержимое", async () => {
    const { user } = setup();
    const dialog = await screen.findByRole("dialog", { name: "Риск RISK-12" });
    await user.click(within(dialog).getByRole("tab", { name: "Планы" }));
    await within(dialog).findByText(risk.mitigation_plan);
    expect(within(dialog).getByText(risk.response_plan)).toBeTruthy();
    expect(within(dialog).queryByRole("heading", { name: "Причина риска" })).toBeNull();
    await user.click(within(dialog).getByRole("tab", { name: "Контроль" }));
    await within(dialog).findByText("Не назначен");
    expect(within(dialog).getByText("Дата не назначена")).toBeTruthy();
    expect(within(dialog).queryByText(risk.mitigation_plan)).toBeNull();
});

it("переходит к связанной задаче без перезаписи её данных просмотра", async () => {
    const { client, user } = setup();
    const taskWithAttention = { id: 7, attention_view: { revision: 12 } };
    client.setQueryData(queryKeys.task(7), taskWithAttention);
    const dialog = await screen.findByRole("dialog", { name: "Риск RISK-12" });
    await user.click(within(dialog).getByRole("tab", { name: "Связи" }));
    const link = await within(dialog).findByRole("button", { name: "DEMO-7 · Согласовать контракт" });
    expect(client.getQueryData(queryKeys.task(7))).toBe(taskWithAttention);
    await user.click(link);
    expect(useUiStore.getState().selectedRisk).toBeNull();
    expect(useUiStore.getState().selectedTaskId).toBe(7);
});

it("сохраняет введённое при ошибке и отменяет редактирование без повторной записи", async () => {
    const { state, user } = setup();
    state.patchStatus = 503;
    const dialog = await screen.findByRole("dialog", { name: "Риск RISK-12" });
    await user.click(within(dialog).getByRole("tab", { name: "Планы" }));
    await user.click(within(dialog).getByRole("button", { name: "Редактировать" }));
    const description = within(dialog).getByLabelText("Описание *", { exact: true });
    await user.clear(description);
    await user.type(description, "Уточнённое описание");
    await user.click(within(dialog).getByRole("button", { name: "Сохранить" }));
    await within(dialog).findByText("Не удалось сохранить изменения");
    expect((description as HTMLTextAreaElement).value).toBe("Уточнённое описание");
    await user.click(within(dialog).getByRole("button", { name: "Отмена" }));
    await within(dialog).findByText(risk.mitigation_plan);
    expect(within(dialog).getByRole("tab", { name: "Планы" }).getAttribute("aria-selected")).toBe("true");
    expect(state.calls.filter((call) => call.method === "PATCH")).toHaveLength(1);
    expect(state.risk.description).toBe(risk.description);
});

it("отправляет только изменения и удерживает окно до окончания сохранения", async () => {
    const { state, user } = setup();
    let release!: () => void;
    state.gate = new Promise<void>((resolve) => { release = resolve; });
    const dialog = await screen.findByRole("dialog", { name: "Риск RISK-12" });
    await user.click(within(dialog).getByRole("button", { name: "Редактировать" }));
    const description = within(dialog).getByLabelText("Описание *", { exact: true });
    await user.clear(description);
    await user.type(description, "Новая причина риска");
    await user.click(within(dialog).getByRole("button", { name: "Сохранить" }));
    await within(dialog).findByRole("button", { name: "Сохранение…" });
    expect(state.calls.find((call) => call.method === "PATCH")?.body).toEqual({ description: "Новая причина риска" });
    await user.click(within(dialog).getByRole("button", { name: "Закрыть риск" }));
    expect(useUiStore.getState().selectedRisk?.riskId).toBe(12);
    await act(async () => release());
    await within(dialog).findByText("Новая причина риска");
    expect(within(dialog).getByRole("button", { name: "Редактировать" })).toBeTruthy();
});

it.each([403, 404])("скрывает кэшированное описание после потери доступа: %i", async (status) => {
    const { state, client } = setup();
    const dialog = await screen.findByRole("dialog", { name: "Риск RISK-12" });
    await within(dialog).findByRole("heading", { name: "Причина риска" });
    state.readStatus = status;
    await act(async () => { await client.invalidateQueries({ queryKey: queryKeys.projectRisk(1, 12) }); });
    await screen.findByText("Риск недоступен");
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Причина риска" })).toBeNull());
    expect(screen.queryByRole("button", { name: "Редактировать" })).toBeNull();
});
