// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it } from "vitest";
import type { AgentMessage, ChatMessageReference } from "@/lib/types";
import { chatMessageReferenceHref } from "@/lib/chatMessageReferences";
import { ChatMessageReferences } from "./ChatMessageReferences";

const reference: ChatMessageReference = {
    kind: "project_chat", chat_id: 9, message_id: 42, project_id: 1, project_key: "DEMO",
    author_id: 7, author_name: "Олег Петров", created_at: "2026-11-14T09:15:00Z", revision: 3,
    content_hash: "server-verified-hash", quote: "😀 Точное условие:\nоплата после приёмки.",
    start: 12, end: 53, offset_unit: "unicode_codepoints", state: "available",
};
afterEach(cleanup);

it("показывает точную серверную цитату, автора и время с собственной ссылкой", () => {
    const payload: Pick<AgentMessage, "message_references"> = { message_references: [reference] };
    const { container } = render(<MemoryRouter><ChatMessageReferences references={payload.message_references} /></MemoryRouter>);
    expect(container.querySelector("blockquote")?.textContent).toBe(reference.quote);
    expect(screen.getByText(reference.author_name)).toBeTruthy();
    expect(container.querySelector("time")?.dateTime).toBe(reference.created_at);
    expect(screen.getByRole("link", { name: "Открыть сообщение" }).getAttribute("href")).toBe("/projects/DEMO/chat?message=42");
});

it.each(["edited", "deleted", "unavailable"] as const)("не раскрывает старый quote при состоянии %s", state => {
    const { container } = render(<MemoryRouter><ChatMessageReferences references={[{ ...reference, state }]} /></MemoryRouter>);
    expect(container.querySelector("blockquote")).toBeNull();
    expect(container.textContent).not.toContain(reference.quote);
    expect(screen.getByRole("status").textContent).toMatch({ edited: /изменено/, deleted: /удалено/, unavailable: /недоступно/ }[state]);
    if (state === "unavailable") {
        expect(screen.queryByRole("link")).toBeNull();
        expect(screen.queryByText(reference.author_name)).toBeNull();
    }
});

it("разделяет общий, проектный приватный и личный маршруты, не подставляет личный для скрытого проекта", () => {
    expect(chatMessageReferenceHref({ ...reference, kind: "conversation" })).toBe("/projects/DEMO/conversations?conversation=9&message=42");
    expect(chatMessageReferenceHref({ ...reference, kind: "conversation", project_id: null, project_key: null })).toBe("/chat?conversation=9&message=42");
    expect(chatMessageReferenceHref({ ...reference, kind: "conversation", project_key: null })).toBeNull();
    expect(chatMessageReferenceHref({ ...reference, message_id: -1 })).toBeNull();
});

it("старый ответ без message_references не создаёт блок цитат", () => {
    const { container } = render(<ChatMessageReferences />);
    expect(container.childElementCount).toBe(0);
});
