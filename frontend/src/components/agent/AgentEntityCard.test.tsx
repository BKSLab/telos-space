// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import type { KnowledgeSource } from "@/lib/types";
import { AgentEntityCard } from "./AgentEntityCard";
import { useUiStore } from "@/stores/ui";

vi.mock("@/components/documents/DocumentView", () => ({ DocumentDialog: ({ documentRef }: { documentRef: string }) => <output role="dialog">{documentRef}</output> }));

afterEach(cleanup);
function Location() { const where = useLocation(); return <output aria-label="Адрес">{where.pathname + where.search}</output>; }
const source: KnowledgeSource = { source_id: "wbs_node:8", entity_type: "wbs_node", entity_id: 8, title: "Раздел ИСР", excerpt: null, score: null, task_id: null };

it("сохраняет переход к разделу ИСР", () => {
    render(<MemoryRouter><AgentEntityCard source={source} project={{ id: 1, key: "TASK" }} /><Location /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "Раздел ИСР: Раздел ИСР" }));
    expect(screen.getByLabelText("Адрес").textContent).toBe("/projects/TASK/structure?node=8");
});

it("личный режим показывает недоступную для перехода карточку без ошибки", () => {
    render(<MemoryRouter><AgentEntityCard source={source} project={null} /></MemoryRouter>);
    expect(screen.getByLabelText("Раздел ИСР: Раздел ИСР")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
});

it("открывает опубликованный отчёт на вкладке результата задачи", () => {
    render(<MemoryRouter><AgentEntityCard source={{ ...source, entity_type: "task_report", source_id: "task_report:5", entity_id: 5, task_id: 5, title: "TASK-5 · Отчёт, версия 2", card: { key: "TASK-5" } }} project={{ id: 1, key: "TASK" }} /><Location /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "Отчёт по задаче: TASK-5 · Отчёт, версия 2" }));
    expect(screen.getByLabelText("Адрес").textContent).toBe("/my-tasks?task=5&task_tab=report");
});

it.each(["document", "project_file", "attachment"] as const)("открывает %s по opaque ref общим просмотрщиком даже в личном чате", type => {
    useUiStore.getState().setSelectedTaskId(null);
    render(<MemoryRouter><AgentEntityCard source={{ ...source, source_id: `${type}:42`, entity_type: type, entity_id: 42, task_id: type === "attachment" ? 7 : null, document_ref: `opaque-${type}`, title: "План.pdf" }} project={null} /><Location /></MemoryRouter>);
    fireEvent.click(screen.getByRole("button", { name: "Документ: План.pdf" }));
    expect(screen.getByRole("dialog").textContent).toBe(`opaque-${type}`);
    expect(screen.getByLabelText("Адрес").textContent).toBe("/");
    expect(useUiStore.getState().selectedTaskId).toBeNull();
});
