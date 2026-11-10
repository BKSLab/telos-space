// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { ProjectFileDetail } from "@/lib/types";
import { useProjectFiles } from "@/lib/useProjectFiles";
import { ProjectFileDialog } from "./ProjectFileDialog";

const clients: QueryClient[] = [];
const stamp = "2026-11-06T10:01:02Z";

beforeEach(() => {
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
    HTMLElement.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
    cleanup();
    clients.splice(0).forEach(client => client.clear());
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
});

function setup(overrides: Partial<ProjectFileDetail> = {}) {
    const state = {
        file: { id: 5, project_id: 1, uploaded_by_user_id: 2, uploaded_by_name: "Иванов Иван", can_edit: true,
            original_name: "План.md", content_type: "text/markdown", size: 1024, created_at: stamp,
            content_url: "/api/v1/projects/1/files/5/content", extraction_status: "disabled", extraction_detail: null,
            fts_status: "ready", index_status: "disabled", index_detail: null, indexed_at: null, summary: null,
            text: "", text_offset: 0, text_total_chars: 0, next_offset: null, ...overrides } as ProjectFileDetail,
        original: "# Исходный план 🛰️\n\n**Решение**\n\nСтрока 2",
        captionError: false, pageError: false, patches: [] as { caption: string | null }[],
        rawSignals: [] as AbortSignal[],
    };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
        const url = new URL(String(input));
        const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
        if (url.pathname.endsWith("/content")) {
            state.rawSignals.push(init!.signal as AbortSignal);
            return new Response(state.original);
        }
        if (url.pathname.endsWith("/files")) return json([state.file]);
        if (init?.method === "PATCH") {
            const payload = JSON.parse(String(init.body));
            state.patches.push(payload);
            if (state.captionError) return json({ detail: "Сеть недоступна" }, 503);
            Object.assign(state.file, payload);
            return json(state.file);
        }
        if (url.searchParams.get("offset") === "8000") return state.pageError ? json({ detail: "Ошибка страницы" }, 503)
            : json({ ...state.file, text: "Последняя страница 🪐\n# Markdown", text_offset: 8000, next_offset: null });
        return json(state.file);
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    clients.push(client);
    function Harness() {
        const model = useProjectFiles(1, "");
        return <ProjectFileDialog projectId={1} fileId={5} onClose={vi.fn()} onDelete={vi.fn()} retry={model.retry} captionMutation={model.caption} />;
    }
    const view = render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>);
    return { state, view };
}

it.each(["processing", "disabled", "failed"] as const)("показывает исходный Markdown независимо от извлечения %s", async extraction_status => {
    setup({ extraction_status });
    const user = userEvent.setup();
    expect(await screen.findByRole("heading", { name: "Исходный план 🛰️" })).toBeTruthy();
    expect(screen.getByText("Загрузил: Иванов Иван")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Документ" }).getAttribute("aria-selected")).toBe("true");
    await user.click(screen.getByRole("tab", { name: "Текстовое представление" }));
    expect(screen.getByRole("tabpanel", { name: "Текстовое представление" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Сохранить комментарий" })).toBeNull();
    await user.click(screen.getByRole("tab", { name: "Документ" }));
    expect(screen.getByRole("heading", { name: "Исходный план 🛰️" })).toBeTruthy();
});

it("сохраняет многострочный черновик при ошибке и смене вкладки, отменяет правку", async () => {
    const { state } = setup();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Добавить комментарий" }));
    const text = "Согласовано\nВторая строка 🪐";
    await user.type(screen.getByRole("textbox", { name: "Комментарий к файлу" }), text);
    state.captionError = true;
    await user.click(screen.getByRole("button", { name: "Сохранить комментарий" }));
    expect(await screen.findByText("Сеть недоступна")).toBeTruthy();
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(text);
    await user.click(screen.getByRole("tab", { name: "Текстовое представление" }));
    await user.click(screen.getByRole("tab", { name: "Документ" }));
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(text);
    state.captionError = false;
    await user.click(screen.getByRole("button", { name: "Сохранить комментарий" }));
    await screen.findByRole("button", { name: "Изменить комментарий" });
    expect(state.file.caption).toBe(text);
    await user.click(screen.getByRole("button", { name: "Изменить комментарий" }));
    await user.clear(screen.getByRole("textbox"));
    await user.click(screen.getByRole("button", { name: "Отмена" }));
    expect(screen.getByText(/Согласовано/).textContent).toBe(text);
    expect(state.patches).toHaveLength(2);
});

it("читает последнюю страницу Unicode/Markdown и сохраняет оригинал при ошибке следующей страницы", async () => {
    const { state } = setup({ extraction_status: "ready", text: "Первая страница", text_total_chars: 8034, next_offset: 8000 });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("tab", { name: "Текстовое представление" }));
    state.pageError = true;
    await user.click(screen.getByRole("button", { name: "Далее" }));
    await screen.findByText("Ошибка страницы");
    await user.click(screen.getByRole("tab", { name: "Документ" }));
    expect(await screen.findByRole("heading", { name: "Исходный план 🛰️" })).toBeTruthy();
    await user.click(screen.getByRole("tab", { name: "Текстовое представление" }));
    state.pageError = false;
    await user.click(screen.getByRole("button", { name: "Повторить загрузку текста" }));
    expect((await screen.findByLabelText("Извлечённый текст")).textContent).toBe("Последняя страница 🪐\n# Markdown");
    expect((screen.getByRole("button", { name: "Далее" }) as HTMLButtonElement).disabled).toBe(true);
    await user.click(screen.getByRole("button", { name: "Назад" }));
    await waitFor(() => expect(screen.getByLabelText("Извлечённый текст").textContent).toBe("Первая страница"));
});

it("скрывает изменения без прав и объясняет ошибку защищённого изображения", async () => {
    setup({ can_edit: false, uploaded_by_name: null, original_name: "Фото.png", content_type: "image/png", previewable: true, preview_url: "/api/v1/projects/1/files/5/preview" });
    expect(await screen.findByText("Загрузил: Автор недоступен")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Добавить комментарий" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Удалить" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Повторить обработку" })).toBeNull();
    fireEvent.error(screen.getByAltText("Фото.png"));
    expect(screen.getByText("Не удалось загрузить изображение")).toBeTruthy();
    expect(within(screen.getByRole("dialog")).getByRole("link", { name: "Скачать оригинал" })).toBeTruthy();
});

it("отменяет незавершённое чтение оригинала при закрытии карточки", async () => {
    const { state, view } = setup();
    vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => {
        if (String(input).endsWith("/content")) return new Promise<Response>((_resolve, reject) => {
            const signal = init!.signal as AbortSignal;
            state.rawSignals.push(signal);
            signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
        });
        return new Response(JSON.stringify(state.file));
    });
    await waitFor(() => expect(state.rawSignals).toHaveLength(1));
    view.unmount();
    await waitFor(() => expect(state.rawSignals[0].aborted).toBe(true));
});

it("очищает исходный Markdown общим рендерером", async () => {
    const { state } = setup();
    state.original = '# Безопасный текст\n\n<script>globalThis.injected = true</script>\n\n<img src="x" onerror="alert(1)">';
    await screen.findByRole("heading", { name: "Безопасный текст" });
    const original = screen.getByRole("region", { name: "Содержимое оригинала" });
    expect(original.querySelector("script")).toBeNull();
    expect(original.querySelector("[onerror]")).toBeNull();
});
