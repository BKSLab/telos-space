// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { useChatContext } from "@/lib/chatContext";
import type { ChatMessage } from "@/lib/projectChat";
import type { KnowledgeSource } from "@/lib/types";
import { useUiStore } from "@/stores/ui";
import { AgentEntityCard } from "@/components/agent/AgentEntityCard";
import { ChatContextProvider } from "./ChatContextProvider";

vi.mock("@/lib/useCalendarView", () => ({ useMediaQuery: () => true }));
afterEach(() => { cleanup(); useUiStore.getState().setSelectedTaskId(null); });
const source = (id: number): KnowledgeSource => ({ source_id: `task:${id}`, entity_type: "task", entity_id: id, task_id: id, title: `Задача ${id}`, excerpt: null, score: null, document_slug: null });
const answer = (id: number, taskId: number) => ({ id, revision: 1, entities: [], deleted_at: null, agent: { status: "completed", sources: [source(taskId)] } } as unknown as ChatMessage);
function Controls() {
    const context = useChatContext();
    return <><output aria-label="Выбранный объект">{context?.target?.id ?? "пусто"}</output>
        <output aria-label="Панель видна">{String(context?.isOpen ?? false)}</output>
        <button onClick={() => context?.togglePin()}>Закрепить</button>
        <button onClick={() => context?.close()}>Скрыть</button>
        <button onClick={() => context?.show()}>Показать</button>
        <AgentEntityCard source={source(77)} project={{ id: 1, key: "DEMO" }} /></>;
}
function Workspace({ messages, enabled = true }: { messages: ChatMessage[]; enabled?: boolean }) {
    return <MemoryRouter><ChatContextProvider projectId={1} projectKey="DEMO" messages={messages} enabled={enabled}><Controls /></ChatContextProvider></MemoryRouter>;
}
const selected = () => screen.getByLabelText("Выбранный объект").textContent;

it("показывает новую единственную задачу и удерживает ручной выбор до следующего ответа", () => {
    const first = answer(1, 42);
    const view = render(<Workspace messages={[first]} />);
    expect(selected()).toBe("42");
    fireEvent.click(screen.getByRole("button", { name: "Задача: Задача 77" }));
    expect(selected()).toBe("77");
    expect(useUiStore.getState().selectedTaskId).toBeNull();
    view.rerender(<Workspace messages={[first]} />);
    expect(selected()).toBe("77");
    view.rerender(<Workspace messages={[first, answer(2, 43)]} />);
    expect(selected()).toBe("43");
});

it("закрепление защищает выбор от новых ответов, сохраняя ручное переключение", () => {
    const first = answer(1, 42);
    const view = render(<Workspace messages={[first]} />);
    fireEvent.click(screen.getByRole("button", { name: "Закрепить" }));
    view.rerender(<Workspace messages={[first, answer(2, 43)]} />);
    expect(selected()).toBe("42");
    fireEvent.click(screen.getByRole("button", { name: "Задача: Задача 77" }));
    expect(selected()).toBe("77");
    view.rerender(<Workspace messages={[first, answer(2, 43), answer(3, 44)]} />);
    expect(selected()).toBe("77");
    fireEvent.click(screen.getByRole("button", { name: "Закрепить" }));
    expect(selected()).toBe("44");
});

it("скрытие сохраняет выбранный объект и новые ответы не открывают панель", () => {
    const first = answer(1, 42);
    const view = render(<Workspace messages={[first]} />);
    fireEvent.click(screen.getByRole("button", { name: "Скрыть" }));
    view.rerender(<Workspace messages={[first, answer(2, 43)]} />);
    expect(screen.getByLabelText("Панель видна").textContent).toBe("false");
    fireEvent.click(screen.getByRole("button", { name: "Показать" }));
    expect(selected()).toBe("43");
});

it("компактное окно сохраняет прежнее открытие полной карточки", () => {
    render(<Workspace messages={[]} enabled={false} />);
    fireEvent.click(screen.getByRole("button", { name: "Задача: Задача 77" }));
    expect(useUiStore.getState().selectedTaskId).toBe(77);
    expect(selected()).toBe("пусто");
});
