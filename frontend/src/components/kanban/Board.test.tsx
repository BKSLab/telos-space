// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ProjectStage, Task } from "@/lib/types";
import { api, endpoints, queryKeys } from "@/lib/api";
import { Board } from "./Board";

const { notifyError } = vi.hoisted(() => ({ notifyError: vi.fn() }));
vi.mock("@/lib/useRisks", () => ({ useRiskTaskCounts: () => ({ data: {} }) }));
vi.mock("@/lib/toast", () => ({ useToast: () => ({ error: notifyError }) }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); notifyError.mockClear(); });

const stages: ProjectStage[] = [
    { id: 1, project_id: 1, name: "План", color: "#fff", order_index: 0, is_done_stage: false },
    { id: 2, project_id: 1, name: "В работе", color: "#fff", order_index: 1, is_done_stage: false },
];
function task(id: number, stageId: number, position: number): Task {
    return { revision: 1, id, project_id: 1, stage_id: stageId, position, key: `DEMO-${id}`, number: id, title: `Задача ${id}`,
        description_md: null, priority: "MEDIUM", role: null, assignee: null, participants: [], start_date: null, due_date: null,
        baseline_start_date: null, baseline_due_date: null, completed_at: null, created_at: "2026-01-01", updated_at: "2026-01-01",
        comments_count: 0, last_comment: null, search_match_source: null, search_title: null, search_excerpt: null, wbs_node_id: null };
}
function setup() {
    const tasks = [task(10, 1, 1000), task(20, 2, 5000), task(21, 2, 1000)];
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    client.setQueryData(queryKeys.tasks(1, ""), tasks);
    const onOpen = vi.fn();
    render(<QueryClientProvider client={client}><Board projectId={1} stages={stages} tasks={tasks} search="" selectedTaskId={null} onTaskOpen={onOpen} /></QueryClientProvider>);
    return { tasks, client, onOpen };
}

describe("kanban stage picker", () => {
    it("moves without drag, appends after the last task and keeps the task dialog closed", async () => {
        const patch = vi.spyOn(api, "patch").mockResolvedValue(task(10, 2, 6000));
        const { client, onOpen } = setup();
        fireEvent.change(screen.getByLabelText("Стадия задачи DEMO-10"), { target: { value: "2" } });
        await waitFor(() => expect(patch).toHaveBeenCalledWith(endpoints.taskStagePlacement(10), { stage_id: 2, position: 6000 }));
        expect(client.getQueryData<Task[]>(queryKeys.tasks(1, ""))?.find(item => item.id === 10)).toMatchObject({ stage_id: 2, position: 6000 });
        expect(onOpen).not.toHaveBeenCalled();
    });

    it("rolls back the optimistic move when the API rejects it", async () => {
        vi.spyOn(api, "patch").mockRejectedValue(new Error("Нет доступа"));
        const { client, tasks } = setup();
        fireEvent.change(screen.getByLabelText("Стадия задачи DEMO-10"), { target: { value: "2" } });
        await waitFor(() => expect(notifyError).toHaveBeenCalledWith("Не удалось переместить задачу: Нет доступа"));
        expect(client.getQueryData(queryKeys.tasks(1, ""))).toEqual(tasks);
    });

    it("does not write when the selected stage has not changed", () => {
        const patch = vi.spyOn(api, "patch");
        setup();
        fireEvent.change(screen.getByLabelText("Стадия задачи DEMO-10"), { target: { value: "1" } });
        expect(patch).not.toHaveBeenCalled();
    });
});
