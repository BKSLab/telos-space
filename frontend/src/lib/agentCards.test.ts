import { expect, it } from "vitest";
import { actionSources } from "./agentCards";
import type { AgentToolRun } from "./types";

const action = (result: Record<string, unknown>): AgentToolRun => ({
    id: "action-1", tool_name: "read_project_file", title: "Файл проекта", arguments: {},
    status: "completed", result, created_at: "2026-11-06T10:00:00Z",
});

it("создаёт источник самостоятельного файла по ID файла", () => {
    const sources = actionSources(action({ project_file: { id: 5, original_name: "План.pdf", project_id: 1 } }));
    expect(sources).toMatchObject([{ source_id: "project_file:5", entity_type: "project_file", entity_id: 5, title: "План.pdf", task_id: null, document_slug: null }]);
});

it("сохраняет прежние карточки вложений задач", () => {
    const sources = actionSources(action({ attachment: { id: 8, original_name: "Вложение.pdf", task_id: 12 } }));
    expect(sources).toMatchObject([{ source_id: "attachment:8", entity_type: "attachment", entity_id: 8, task_id: 12 }]);
});
