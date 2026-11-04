// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { newChecklistItem } from "@/lib/checklists";
import { useAgentChatStore } from "@/stores/agentChat";
import { emptyChatDraft, useChatUiStore } from "@/stores/chat";

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("черновики по HTTP без crypto.randomUUID", () => {
    beforeEach(() => {
        const cryptoApi = globalThis.crypto;
        vi.stubGlobal("crypto", { getRandomValues: cryptoApi.getRandomValues.bind(cryptoApi) });
        useAgentChatStore.setState({ drafts: {} });
        useChatUiStore.setState({ drafts: {} });
    });
    afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear(); });

    it("создаёт и сохраняет черновик общего чата", () => {
        const draft = emptyChatDraft();
        expect(draft.clientMessageId).toMatch(UUID_V4);
        useChatUiStore.getState().updateDraft("1:2", { content: "Сообщение по HTTP" });
        expect(useChatUiStore.getState().drafts["1:2"].clientMessageId).toMatch(UUID_V4);
        expect(useChatUiStore.getState().drafts["1:2"].content).toBe("Сообщение по HTTP");
    });

    it("сохраняет запрос Арису и не теряет новый черновик при старом ответе", () => {
        useAgentChatStore.getState().updateDraft("1:personal", { content: "Первый вопрос" });
        const first = useAgentChatStore.getState().drafts["1:personal"].requestId;
        useAgentChatStore.getState().updateDraft("1:personal", { content: "Новый вопрос" });
        const second = useAgentChatStore.getState().drafts["1:personal"].requestId;
        expect(first).toMatch(UUID_V4);
        expect(second).toMatch(UUID_V4);
        expect(second).not.toBe(first);
        useAgentChatStore.getState().clearDraft("1:personal", first);
        expect(useAgentChatStore.getState().drafts["1:personal"].content).toBe("Новый вопрос");
    });

    it("добавляет независимые пункты чек-листа", () => {
        const first = newChecklistItem();
        const second = newChecklistItem();
        expect(first.id).toMatch(UUID_V4);
        expect(second.id).toMatch(UUID_V4);
        expect(first.id).not.toBe(second.id);
        expect(first.is_completed).toBe(false);
    });
});
