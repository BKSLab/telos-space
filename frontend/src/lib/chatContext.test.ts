import { describe, expect, it } from "vitest";
import type { ChatEntity, ChatMessage } from "./projectChat";
import type { KnowledgeSource } from "./types";
import { entityContextTarget, recentChatContext, sourceContextTarget, suggestedChatContext } from "./chatContext";

const source = (id: number, type: KnowledgeSource["entity_type"] = "task"): KnowledgeSource => ({
    source_id: `${type}:${id}`, entity_type: type, entity_id: id, task_id: type === "task" ? id : null,
    title: `Объект ${id}`, excerpt: "Фрагмент", score: null,
    document_ref: ["document", "project_file", "attachment"].includes(type) ? `opaque-${type}-${id}` : null,
});
const entity: ChatEntity = { entity_type: "TASK", entity_id: 42, available: true, title: "Задача 42", href: null, status: null, subtitle: null };
function answer(id: number, sources: KnowledgeSource[]): ChatMessage {
    return { id, seq: id, revision: 1, deleted_at: null, entities: [], agent: { status: "completed", sources } } as unknown as ChatMessage;
}

describe("контекст из карточек переписки", () => {
    it("документы всех хранилищ используют opaque ref, задачи сохраняют id", () => {
        expect(sourceContextTarget(source(7, "document"))).toEqual({ type: "document", title: "Объект 7", documentRef: "opaque-document-7", fragment: undefined });
        expect(sourceContextTarget(source(9, "project_file"))?.type).toBe("document");
        expect(sourceContextTarget(source(9, "attachment"))?.type).toBe("document");
        expect(sourceContextTarget({ ...source(99), task_id: 42 })).toMatchObject({ type: "task", id: 42 });
    });
    it("отбрасывает недоступные карточки, неподдерживаемые типы и неверные id", () => {
        expect(entityContextTarget({ ...entity, available: false })).toBeNull();
        expect(entityContextTarget({ ...entity, entity_id: -1 })).toBeNull();
        expect(sourceContextTarget(source(1.5))).toBeNull();
        expect(sourceContextTarget(source(5, "task_report"))).toBeNull();
        expect(sourceContextTarget(source(5, "risk"))).toBeNull();
        expect(sourceContextTarget({ ...source(7, "document"), document_ref: null })).toBeNull();
    });
    it("объединяет повторы из источников и ссылок, исключая удалённые и локальные сообщения", () => {
        const history = [answer(1, [source(42)]), { ...answer(2, []), entities: [entity] },
            { ...answer(3, [source(9, "project_file")]), deleted_at: "2026-11-06" },
            { ...answer(4, [source(3)]), delivery: "sending" as const }];
        expect(recentChatContext(history)).toEqual([{ type: "task", id: 42, title: "Задача 42" }]);
        expect(recentChatContext([answer(5, [source(1), source(2), source(3)])], 2)).toHaveLength(2);
    });
    it("автоматически выбирает только однозначный завершённый ответ", () => {
        const first = answer(1, [source(42)]);
        const newer = answer(2, [source(7, "document")]);
        expect(suggestedChatContext([first, newer])?.target).toMatchObject({ documentRef: "opaque-document-7" });
        expect(suggestedChatContext([{ ...newer, agent: { ...newer.agent!, status: "processing" } }])).toBeNull();
        expect(suggestedChatContext([answer(3, [source(1), source(2)])])).toBeNull();
        expect(suggestedChatContext([answer(4, [source(42), source(42)])])?.target).toMatchObject({ type: "task", id: 42 });
        // Unrelated replies do not clear a useful existing context.
        expect(suggestedChatContext([first, answer(5, [])])?.key).toBe("1:1");
    });
    it("использует основной документ при нескольких источниках и не смешивает одинаковые numeric id", () => {
        const document = source(9, "document");
        const attachment = { ...source(9, "attachment"), is_primary: true };
        const history = [answer(1, [document, attachment])];
        expect(recentChatContext(history)).toHaveLength(2);
        expect(suggestedChatContext(history)?.target).toMatchObject({ documentRef: "opaque-attachment-9" });
        expect(suggestedChatContext([answer(2, [{ ...document, is_primary: true }, attachment])])).toBeNull();
    });
});
