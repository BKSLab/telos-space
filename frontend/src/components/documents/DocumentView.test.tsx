// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import type { DocumentFragment } from "@/lib/types";
import { DocumentDialog, DocumentView } from "./DocumentView";
import { documentFixture, documentTextFixture } from "./documentFixtures.test-utils";

const clients: QueryClient[] = [];
HTMLElement.prototype.scrollIntoView = vi.fn();
function mount(content: React.ReactNode) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    clients.push(client);
    return { client, ...render(<QueryClientProvider client={client}><MemoryRouter>{content}</MemoryRouter></QueryClientProvider>) };
}
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); });

it("показывает проверенный серверный фрагмент исходника и прокручивает к нему", async () => {
    const page = documentTextFixture();
    const points = Array.from(page.text); const start = points.indexOf("🧭");
    const fragment: DocumentFragment = { document_ref: page.ref, rendition: page.rendition, content_hash: page.content_hash, start, end: start + 1, quote: "🧭", offset_unit: "unicode_codepoints" };
    vi.spyOn(api, "get").mockImplementation(async path => path.includes("/text?") ? page : documentFixture());
    mount(<DocumentView documentRef={page.ref} fragment={fragment} />);
    expect(await screen.findByText("Показан фрагмент текущей версии документа.")).toBeTruthy();
    expect(document.querySelector("mark")?.textContent).toBe("🧭");
    await waitFor(() => expect(HTMLElement.prototype.scrollIntoView).toHaveBeenCalled());
});

it("не подсвечивает и не подменяет цитатой источник после смены хэша", async () => {
    const page = documentTextFixture({ text: "Новая версия документа" });
    const get = vi.spyOn(api, "get").mockImplementation(async path => path.includes("/text?") ? page : documentFixture());
    mount(<DocumentView documentRef={page.ref} fragment={{ document_ref: page.ref, rendition: page.rendition, content_hash: "old-hash", start: 20000, end: 20005, quote: "Старая цитата", offset_unit: "unicode_codepoints" }} />);
    expect(await screen.findByText(/Документ изменился/)).toBeTruthy();
    expect(document.querySelector("mark")).toBeNull();
    expect((await screen.findByLabelText("Исходный текст Markdown")).textContent).toBe("Новая версия документа");
    expect(screen.queryByText("Старая цитата")).toBeNull();
    expect(get.mock.calls.find(([path]) => path.includes("/text?"))?.[0]).toContain("offset=0&");
});

it("не раскрывает документ в прежнем проектном контексте и не запрашивает текст", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue(documentFixture({ title: "Приватное название", ownership: { kind: "project", project_id: 2, owner_task_id: null } }));
    mount(<DocumentView documentRef="test-opaque-document" expectedProjectId={1} />);
    expect(await screen.findByText("Документ недоступен в этом проекте")).toBeTruthy();
    expect(screen.queryByText("Приватное название")).toBeNull();
    expect(get).toHaveBeenCalledTimes(1);
});

it("сохраняет черновик при конфликте версии и запрашивает весь Markdown страницами", async () => {
    const get = vi.spyOn(api, "get").mockImplementation(async path => {
        if (!path.includes("/text?")) return documentFixture({ revision: 4 });
        return path.includes("offset=5") ? documentTextFixture({ text: "вторая", offset: 5, total_chars: 11 }) : documentTextFixture({ text: "перва", next_offset: 5, total_chars: 11 });
    });
    const patch = vi.spyOn(api, "patch").mockRejectedValue(new Error("Версия документа изменилась"));
    mount(<DocumentView documentRef="test-opaque-document" />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Редактировать" }));
    const editor = await screen.findByLabelText("Содержимое документа в Markdown");
    expect((editor as HTMLTextAreaElement).value).toBe("первавторая");
    fireEvent.change(editor, { target: { value: "Мой черновик" } });
    await user.click(screen.getByRole("button", { name: "Сохранить документ" }));
    expect(await screen.findByText(/Черновик сохранён/)).toBeTruthy();
    expect((editor as HTMLTextAreaElement).value).toBe("Мой черновик");
    expect(patch).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ expected_revision: 4, content_md: "Мой черновик" }));
    expect(get.mock.calls.some(([path]) => path.includes("offset=5"))).toBe(true);
});

it("общий диалог закрывается по Escape и возвращает фокус", async () => {
    vi.spyOn(api, "get").mockImplementation(async path => path.includes("/text?") ? documentTextFixture() : documentFixture());
    const onClose = vi.fn();
    mount(<><button type="button" autoFocus>Документы</button><DocumentDialog documentRef="test-opaque-document" onClose={onClose} /></>);
    await screen.findByRole("dialog", { name: "Документ" });
    fireEvent.keyDown(document.activeElement!, { key: "Escape", code: "Escape" });
    await waitFor(() => expect(onClose).toHaveBeenCalled());
});

it("после сохранения возвращает фокус только доступной кнопке без ожидания animation frame; Escape закрывает диалог", async () => {
    vi.spyOn(api, "get").mockImplementation(async path => path.includes("/text?") ? documentTextFixture() : documentFixture());
    let resolveSave!: (value: unknown) => void;
    vi.spyOn(api, "patch").mockReturnValue(new Promise(resolve => { resolveSave = resolve; }));
    const onClose = vi.fn(); const user = userEvent.setup();
    mount(<DocumentDialog documentRef="test-opaque-document" onClose={onClose} />);
    await user.click(await screen.findByRole("button", { name: "Редактировать" }));
    const save = await screen.findByRole("button", { name: "Сохранить документ" });
    await user.click(save);
    expect((save as HTMLButtonElement).disabled).toBe(true);
    const frame = vi.spyOn(globalThis, "requestAnimationFrame").mockReturnValue(0);
    await act(async () => { resolveSave(documentFixture({ revision: 2 })); });
    const edit = await screen.findByRole("button", { name: "Редактировать" });
    await waitFor(() => expect(document.activeElement).toBe(edit));
    expect((edit as HTMLButtonElement).disabled).toBe(false);
    frame.mockRestore();
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
});
