// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { queryKeys } from "@/lib/api";
import { formatDateTime } from "@/lib/dates";
import type { AnalyticsReport, AnalyticsTaskRef } from "@/lib/types";
import { ProjectSummaryPanel } from "./ProjectSummaryPanel";

function task(id: number): AnalyticsTaskRef {
    return { id, key: `DEMO-${id}`, title: `Основание для вывода ${id}`, project_key: "DEMO", due_date: "2020-01-02", is_overdue: true };
}

const report: AnalyticsReport = {
    id: 41, scope: "PROJECT", project_id: 7, project_key: "DEMO", project_name: "Тестовый проект",
    created_at: "2020-01-03T10:15:00Z", created_by: "Автор сохранённого разбора", llm_model: "saved-test-model", duration_ms: 12500,
    headline: "Сначала устранить зависимость", health: "RISK", health_note: "Полная оценка состояния.\nВторой абзац содержит основание и не должен теряться.",
    findings: [
        { kind: "BLOCKER", severity: "HIGH", title: "Блокирующая зависимость", detail: "Подтверждение из обсуждения первой задачи.", project_key: "DEMO", project_name: "Тестовый проект", tasks: [task(11)] },
        { kind: "DATA_GAP", severity: "LOW", title: "Недостаточно данных", detail: "Для второй задачи отсутствует согласование.", project_key: "DEMO", project_name: "Тестовый проект", tasks: [task(12)] },
    ],
    progress: [{ title: "Контракт принят", detail: "Сохранённый результат проверки третьей задачи.", project_key: "DEMO", project_name: "Тестовый проект", tasks: [task(13)] }],
    recommendations: [
        { horizon: "TODAY", title: "Обсудить зависимость", detail: "Назначить встречу с владельцем четвёртой задачи.", project_key: "DEMO", project_name: "Тестовый проект", tasks: [task(14)] },
        { horizon: "WEEK", title: "Проверить критерии", detail: "Уточнить условия завершения пятой задачи.", project_key: "DEMO", project_name: "Тестовый проект", tasks: [task(15)] },
        { horizon: "LATER", title: "Согласовать резервный план", detail: "Сохранить порядок действий для шестой задачи.", project_key: "DEMO", project_name: "Тестовый проект", tasks: [task(16)] },
    ],
    signals: { total_tasks: 6, done_tasks: 2, overdue_tasks: 3, due_soon_tasks: 0, no_due_date_tasks: 1, unassigned_tasks: 0, stale_tasks: 0, blocked_tasks: 1, milestones_at_risk: 0, unplaced_tasks: 0, high_risks: 2 },
    context: { projects: 1, tasks_total: 6, tasks_included: 4, comments_included: 3, documents_included: 2, stickers_included: 1, milestones_included: 2, activity_included: 5,
        wbs_nodes_included: 2, risks_total: 3, risks_included: 2, truncated: true,
        omitted: ["Два обсуждения не вошли из-за ограничения объёма.", "Старый документ не был доступен."],
        entity_counts: { tasks: { total: 6, included: 4 }, members: { total: 3, included: 2 }, attachments: { total: 4, included: 3 }, document_links: { total: 2, included: 1 } } },
};

const clients: QueryClient[] = [];
function setup(value: AnalyticsReport = report, projectId: number | null = 7) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    clients.push(client);
    client.setQueryData(queryKeys.dashboardAnalytics(projectId), value);
    const onOpenTask = vi.fn();
    const result = render(<QueryClientProvider client={client}><ProjectSummaryPanel projectId={projectId} dataUpdatedAt="2021-01-01T00:00:00Z" onOpenTask={onOpenTask} /></QueryClientProvider>);
    return { client, onOpenTask, ...result };
}

afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); });

it.each([7, null])("показывает две рекомендации в компактном превью (%s) без признаков выполненной работы", projectId => {
    const { container } = setup(report, projectId);
    const preview = screen.getByRole("region", { name: "Рекомендации из разбора" });
    expect(within(preview).getByText("Обсудить зависимость")).toBeTruthy();
    expect(within(preview).getByText("Проверить критерии")).toBeTruthy();
    expect(within(preview).queryByText("Согласовать резервный план")).toBeNull();
    expect(within(preview).getByText("Сегодня")).toBeTruthy();
    expect(within(preview).queryByRole("checkbox")).toBeNull();
    expect(preview.querySelector('[aria-checked], input, [class*="circle-check"]')).toBeNull();
    expect(container.querySelector(".aris-summary-details")?.hasAttribute("open")).toBe(false);
    expect(screen.getByText(report.headline)).toBeTruthy();
    expect(container.querySelector(".aris-summary-panel")?.getAttribute("data-freshness")).toBe("stale");
});

it("раскрывает каждый пункт, основание и задачу полного отчёта без повторения текста оценки", async () => {
    const user = userEvent.setup();
    const { onOpenTask, container } = setup();
    await user.click(screen.getByText(/Полный разбор ·/));
    expect(screen.getAllByText(/Полная оценка состояния/)).toHaveLength(1);
    expect(screen.getByRole("img", { name: "Сохранённый снимок: завершено 2 из 6 задач" })).toBeTruthy();
    expect(screen.getByText("Сохранённые количества; сигналы могут пересекаться.")).toBeTruthy();
    expect(screen.getByText("Высокая важность")).toBeTruthy();
    expect(screen.getByText("Низкая важность")).toBeTruthy();
    const detail = within(container.querySelector<HTMLElement>(".aris-report-detail")!);
    for (const item of [...report.findings, ...report.progress, ...report.recommendations]) {
        expect(detail.getByRole("heading", { name: item.title })).toBeTruthy();
        expect(detail.getByText(item.detail)).toBeTruthy();
        for (const reference of item.tasks) {
            const button = detail.getByRole("button", { name: reference.key, exact: true });
            expect(button.getAttribute("aria-description")).toContain("Просрочена на момент разбора");
            expect(within(button).getByText(reference.title)).toBeTruthy();
            await user.click(button);
            expect(onOpenTask).toHaveBeenLastCalledWith(reference.id);
        }
    }
    expect(detail.getAllByRole("article")).toHaveLength(6);
    expect(within(detail.getByRole("region", { name: "Рекомендации" })).queryByRole("checkbox")).toBeNull();
});

it("сохраняет автора, дату, полный охват источников и все ограничения контекста", async () => {
    const user = userEvent.setup();
    const { container } = setup();
    await user.click(screen.getByText(/Полный разбор ·/));
    expect(screen.getByText(report.created_by)).toBeTruthy();
    expect(screen.getAllByText(formatDateTime(report.created_at)).length).toBeGreaterThan(0);
    await user.click(screen.getByText("Контекст и источники разбора"));
    const context = within(container.querySelector<HTMLElement>(".aris-context-content")!);
    expect(context.getByText("DEMO · Тестовый проект")).toBeTruthy();
    expect(context.getAllByText("Задач")).toHaveLength(1);
    expect(context.getByText("Участников команды").nextElementSibling?.textContent).toBe("2 из 3");
    expect(context.getByText("Вложений").nextElementSibling?.textContent).toBe("3 из 4");
    expect(context.getByText("Связей документов с задачами").nextElementSibling?.textContent).toBe("1 из 2");
    expect(context.getByText("Рисков").nextElementSibling?.textContent).toBe("2 из 3");
    expect(context.getByText(report.llm_model)).toBeTruthy();
    expect(context.getByText("12,5 с")).toBeTruthy();
    for (const omitted of report.context.omitted) expect(context.getByText(omitted)).toBeTruthy();
});

it("нулевой снимок не изображает завершение задач", async () => {
    const { container } = setup({ ...report, signals: { ...report.signals, total_tasks: 0, done_tasks: 0 } });
    await userEvent.click(screen.getByText(/Полный разбор ·/));
    expect(screen.getByRole("img", { name: "Сохранённый снимок: задач не было" })).toBeTruthy();
    expect(container.querySelector(".aris-snapshot-value")).toBeNull();
    expect(screen.queryByText("100%")).toBeNull();
});

it.each(["PROJECT", "PORTFOLIO"] as const)("сохраняет нужный контекст проекта в пунктах разбора %s", async scope => {
    setup({ ...report, scope, project_key: scope === "PROJECT" ? "DEMO" : null,
        findings: [report.findings[0], { ...report.findings[1], project_key: "OTHER", project_name: "Смежный проект" }] }, scope === "PROJECT" ? 7 : null);
    await userEvent.click(screen.getByText(/Полный разбор ·/));
    const rows = within(screen.getByRole("region", { name: "Риски и препятствия" })).getAllByRole("article");
    expect(Boolean(within(rows[0]).queryByText(/Тестовый проект/))).toBe(scope === "PORTFOLIO");
    expect(within(rows[1]).getByText(/Смежный проект/)).toBeTruthy();
    expect(within(rows[0]).getByRole("button", { name: "DEMO-11" })).toBeTruthy();
});

it("новый сохранённый отчёт обновляет актуальность и сворачивает детали", async () => {
    const { client, container } = setup();
    await userEvent.click(screen.getByText(/Полный разбор ·/));
    act(() => client.setQueryData(queryKeys.dashboardAnalytics(7), { ...report, id: 42, headline: "Новый сохранённый вывод", created_at: "2022-01-01T10:00:00Z" }));
    await waitFor(() => expect(screen.getByText("Новый сохранённый вывод")).toBeTruthy());
    expect(container.querySelector(".aris-summary-panel")?.getAttribute("data-freshness")).toBe("fresh");
    expect(container.querySelector(".aris-summary-details")?.hasAttribute("open")).toBe(false);
    expect(screen.queryByText(/Данные менялись после разбора/)).toBeNull();
});
