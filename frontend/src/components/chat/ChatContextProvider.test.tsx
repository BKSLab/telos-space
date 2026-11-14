// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { useChatContext } from "@/lib/chatContext";
import type { ChatMessage } from "@/lib/projectChat";
import type { KnowledgeSource } from "@/lib/types";
import { useUiStore } from "@/stores/ui";
import { AgentEntityCard } from "@/components/agent/AgentEntityCard";
import { ChatEntityCard } from "./ChatEntityCard";
import { ChatContextProvider } from "./ChatContextProvider";

const { viewport } = vi.hoisted(() => ({ viewport: { desktop: true } }));
vi.mock("@/lib/useCalendarView", () => ({ useMediaQuery: () => viewport.desktop }));
afterEach(() => { cleanup(); useUiStore.getState().setSelectedTaskId(null); viewport.desktop = true; });
const source = (id: number): KnowledgeSource => ({ source_id: `task:${id}`, entity_type: "task", entity_id: id, task_id: id, title: `Задача ${id}`, excerpt: null, score: null });
const answer = (id: number, taskId: number) => ({ id, revision: 1, entities: [], deleted_at: null, agent: { status: "completed", sources: [source(taskId)] } } as unknown as ChatMessage);
function Controls() {
    const context = useChatContext();
    return <><output aria-label="Выбранный объект">{context?.target ? context.target.type === "task" ? context.target.id : context.target.documentRef : "пусто"}</output>
        <output aria-label="Панель видна">{String(context?.isOpen ?? false)}</output>
        <button onClick={() => context?.togglePin()}>Закрепить</button>
        <button onClick={() => context?.close()}>Скрыть</button>
        <button onClick={() => context?.show()}>Показать</button>
        <AgentEntityCard source={source(77)} project={{ id: 1, key: "DEMO" }} />
        <AgentEntityCard source={{ ...source(55), task_id: null }} project={{ id: 1, key: "DEMO" }} />
        <AgentEntityCard source={{ ...source(9), source_id: "document:9", entity_type: "document", task_id: null, title: "План", document_ref: "opaque-plan" }} project={{ id: 1, key: "DEMO" }} />
        <AgentEntityCard source={{ ...source(9), source_id: "attachment:9", entity_type: "attachment", task_id: 42, title: "Условия.md", document_ref: "opaque-attachment" }} project={{ id: 1, key: "DEMO" }} />
        <ChatEntityCard entity={{ entity_type: "TASK", entity_id: 88, available: true, title: "Ссылка на задачу", status: null, subtitle: null, href: null }} projectId={1} /></>;
}
function Workspace({ messages, enabled = true }: { messages: ChatMessage[]; enabled?: boolean }) {
    return <MemoryRouter><ChatContextProvider projectId={1} projectKey="DEMO" messages={messages} enabled={enabled}><Controls /></ChatContextProvider></MemoryRouter>;
}
const selected = () => screen.getByLabelText("Выбранный объект").textContent;

it("показывает новую единственную задачу и удерживает ручной выбор до следующего ответа", () => {
    const first = answer(1, 42);
    const view = render(<Workspace messages={[first]} />);
    expect(selected()).toBe("42");
    expect(useUiStore.getState().selectedTaskId).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Задача: Задача 77" }));
    expect(selected()).toBe("77");
    expect(useUiStore.getState().selectedTaskId).toBe(77);
    view.rerender(<Workspace messages={[first]} />);
    expect(selected()).toBe("77");
    view.rerender(<Workspace messages={[first, answer(2, 43)]} />);
    expect(selected()).toBe("43");
    expect(useUiStore.getState().selectedTaskId).toBe(77);
});

it("ссылка на задачу в сообщении открывает общую карточку и сохраняет контекст", () => {
    render(<Workspace messages={[]} />);
    fireEvent.click(screen.getByRole("button", { name: "Задача: Ссылка на задачу" }));
    expect(useUiStore.getState().selectedTaskId).toBe(88);
    expect(selected()).toBe("88");
});

it("источник задачи использует entity_id, если отдельный task_id не задан", () => {
    render(<Workspace messages={[]} />);
    fireEvent.click(screen.getByRole("button", { name: "Задача: Задача 55" }));
    expect(useUiStore.getState().selectedTaskId).toBe(55);
    expect(selected()).toBe("55");
});

it("на телефоне задача открывает карточку, сохраняя контекст без второго листа", () => {
    viewport.desktop = false;
    render(<Workspace messages={[answer(1, 42)]} />);
    fireEvent.click(screen.getByRole("button", { name: "Задача: Ссылка на задачу" }));
    expect(useUiStore.getState().selectedTaskId).toBe(88);
    expect(selected()).toBe("88");
    expect(screen.getByLabelText("Панель видна").textContent).toBe("false");
    fireEvent.click(screen.getByRole("button", { name: "Показать" }));
    expect(screen.getByLabelText("Панель видна").textContent).toBe("true");
});

it("документ продолжает открывать контекст без модальной карточки задачи", () => {
    render(<Workspace messages={[]} />);
    fireEvent.click(screen.getByRole("button", { name: "Документ: План" }));
    expect(selected()).toBe("opaque-plan");
    expect(useUiStore.getState().selectedTaskId).toBeNull();
});

it("вложение задачи открывает документ в контексте, сохраняя задачу закрытой", () => {
    render(<Workspace messages={[]} />);
    fireEvent.click(screen.getByRole("button", { name: "Документ: Условия.md" }));
    expect(selected()).toBe("opaque-attachment");
    expect(useUiStore.getState().selectedTaskId).toBeNull();
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
