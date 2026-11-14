// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import type { ChatMessage as Message } from "@/lib/projectChat";
import { ChatMessage } from "./ChatMessage";

vi.mock("@/lib/useProjectChat", () => ({
    useProjectChat: () => ({ projectId: 1, userId: 1, canWrite: false, state: "connected" }),
}));
vi.mock("./ArisChatReply", () => ({ ArisChatReply: ({ message }: { message: Message }) => <p>{message.content}</p> }));

const message: Message = {
    id: 1, chat_id: 1, project_id: 1, seq: 1, author_type: "USER", author: { id: 1, username: "anna", display_name: "Анна Иванова" },
    client_message_id: "message-id", content: "План проверки готов", revision: 1, created_at: "2026-11-03T10:00:00Z",
    edited_at: null, deleted_at: null, reply: null, mentions: [], entities: [], attachments: [], reactions: [],
};

afterEach(cleanup);

function setup(row: Message) {
    return render(<MemoryRouter><ChatMessage message={row} onReply={vi.fn()} onQuote={vi.fn()} onEdit={vi.fn()} onJump={vi.fn()} /></MemoryRouter>);
}

it("подписывает сообщение Ариса и показывает его декоративный аватар при пустом author", () => {
    const { container } = setup({ ...message, author_type: "AGENT", author: null });
    expect(screen.getByRole("article", { name: "Сообщение Ариса" })).toBeTruthy();
    expect(screen.getByText("Арис", { exact: true })).toBeTruthy();
    const avatar = container.querySelector("img");
    expect(avatar?.getAttribute("src")).toMatch(/\/brand\/aris-avatar-/);
    expect(avatar?.closest('[aria-hidden="true"]')).toBeTruthy();
});

it("сохраняет имя и инициалы участника в его сообщении", () => {
    const { container } = setup(message);
    expect(screen.getByRole("article", { name: "Сообщение Анна Иванова" })).toBeTruthy();
    expect(screen.getByText("АИ", { exact: true })).toBeTruthy();
    expect(container.querySelector("img")).toBeNull();
});

it.each([1, 2, 7])("показывает фото автора %s независимо от присутствия в текущей команде", (id) => {
    const { container } = setup({ ...message, author: { ...message.author!, id, has_avatar: true } });
    const avatar = container.querySelector("img");
    expect(new URL(avatar!.getAttribute("src")!, window.location.origin).pathname).toBe(`/api/v1/users/${id}/avatar`);
    expect(avatar?.className).toContain("size-8");
    expect(avatar?.className).toContain("rounded-lg");
    expect(screen.queryByText("АИ", { exact: true })).toBeNull();
    fireEvent.error(avatar!);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("АИ", { exact: true })).toBeTruthy();
});

it("не загружает фотографию при её отсутствии или удалённом авторе", () => {
    const { container, rerender } = setup({ ...message, author: { ...message.author!, has_avatar: false } });
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("АИ", { exact: true })).toBeTruthy();
    rerender(<MemoryRouter><ChatMessage message={{ ...message, author: null }} onReply={vi.fn()} onQuote={vi.fn()} onEdit={vi.fn()} onJump={vi.fn()} /></MemoryRouter>);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("?", { exact: true })).toBeTruthy();
});
