// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import type { FileSearchItem } from "@/lib/types";
import { useUiStore } from "@/stores/ui";
import { FileSearchResults } from "./FileSearchResults";

afterEach(() => { cleanup(); vi.restoreAllMocks(); useUiStore.getState().setSelectedTaskId(null); });
const item: FileSearchItem = { source_type: "task_attachment", source_id: 9, project_id: 1, task_id: 5, task_key: "DEMO-5", task_title: "Согласовать договор", document_slug: null,
    title: "Условия.docx", caption: "Для встречи", content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", preview_url: null, excerpt: "Новый __FTS_START__пилот__FTS_END__", rank: 2 };
function Location() { return <output aria-label="Адрес">{useLocation().pathname}</output>; }

it("показывает источник из ответа поиска и открывает задачу без дополнительных запросов", async () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    const user = userEvent.setup();
    render(<MemoryRouter><FileSearchResults items={[item]} projectKey="DEMO" /></MemoryRouter>);
    expect(screen.getByText("DOCX")).toBeTruthy();
    expect(document.querySelector("mark")?.textContent).toBe("пилот");
    await user.click(screen.getByRole("button", { name: "DEMO-5 · Согласовать договор" }));
    expect(useUiStore.getState().selectedTaskId).toBe(5);
    expect(fetch).not.toHaveBeenCalled();
});

it("переходит к документу по серверному slug без загрузки списка документов", async () => {
    const fetch = vi.spyOn(globalThis, "fetch");
    const user = userEvent.setup();
    render(<MemoryRouter><FileSearchResults items={[{ ...item, source_type: "document", source_id: 22, title: "Решения", document_slug: "decisions", content_type: "text/markdown" }]} projectKey="DEMO" /><Location /></MemoryRouter>);
    await user.click(screen.getByRole("button", { name: "Открыть документ Решения" }));
    expect(screen.getByLabelText("Адрес").textContent).toBe("/projects/DEMO/docs/decisions");
    expect(fetch).not.toHaveBeenCalled();
});

it("результат поиска файла проекта открывает ту же карточку, включая изображение", async () => {
    const openFile = vi.fn();
    const user = userEvent.setup();
    render(<MemoryRouter><FileSearchResults items={[{ ...item, source_type: "project_file", source_id: 7,
        title: "Фото.png", content_type: "image/png", preview_url: "/api/v1/projects/1/files/7/preview" }]} onOpenProjectFile={openFile} /></MemoryRouter>);
    await user.click(screen.getByRole("button", { name: "Открыть файл Фото.png" }));
    expect(openFile).toHaveBeenCalledWith(7);
    expect(screen.queryByRole("dialog")).toBeNull();
});
