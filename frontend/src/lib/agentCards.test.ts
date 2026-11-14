import { expect, it } from "vitest";
import { actionSources } from "./agentCards";
import type { AgentToolRun } from "./types";

const action = (result: Record<string, unknown>): AgentToolRun => ({
    id: "action-1", tool_name: "telos_create_document", title: "Документ", arguments: {},
    status: "completed", result, created_at: "2026-11-06T10:00:00Z",
});

it.each(["document", "project_file", "attachment"])("создаёт карточку результата %s по canonical DTO и ref", kind => {
    const sources = actionSources(action({ source_id: `${kind}:5`, document_ref: "opaque-ref", document: { ref: "opaque-ref", title: "План", caption: "Условия" } }));
    expect(sources).toMatchObject([{ source_id: `${kind}:5`, entity_type: kind, entity_id: 5, title: "План", task_id: null, document_ref: "opaque-ref", card: { summary: "Условия" } }]);
});

it("показывает документ из проверенного preview ожидания", () => {
    const pending = { ...action({}), status: "pending", result: { preview: { source_id: "attachment:8", document: { ref: "opaque-attachment", title: "Условия.pdf" } } } } as AgentToolRun;
    expect(actionSources(pending)).toMatchObject([{ source_id: "attachment:8", document_ref: "opaque-attachment" }]);
});
