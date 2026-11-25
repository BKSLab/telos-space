// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { api, ApiError } from "@/lib/api";
import type { GitFilePage } from "@/lib/projectKnowledge";
import { SourceViewer, type SourceSelection } from "./SourceViewer";
import { RepositoryAccess, repositoryKey } from "./repositoryApi";

const highlight = vi.hoisted(() => ({ fail: false }));
vi.mock("./MonacoSource", () => ({ default: ({ text, path, line, end }: { text: string; path: string; line: number; end: number }) => {
    if (highlight.fail) throw new Error("Editor unavailable");
    return <pre aria-label={`Подсвеченный код ${path}`} data-range={`${line}-${end}`}>{text}</pre>;
} }));
vi.mock("@/lib/clipboard", () => ({ copyText: vi.fn().mockResolvedValue(undefined) }));
const sha = "a".repeat(40);
const source: SourceSelection = { connection: 2, sha, path: "src/example.py", line: 55, end: 56, offset: 0 };
const page: GitFilePage = { path: source.path, commit_sha: sha, line_start: 55, line_end: 57, offset: 100, next_offset: 120, total_chars: 9000, text: "😀Привет\r\nprint('hello')\r\nчасть", source_url: "https://github.com/owner/repo/blob/sha/src/example.py", stale: false, partial: false };
const clients: QueryClient[] = [];
function mount(selection = source, onRange = vi.fn(), revoke = vi.fn()) {
    const onPage = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    clients.push(client);
    render(<QueryClientProvider client={client}><RepositoryAccess.Provider value={revoke}><SourceViewer project={7} selection={selection} onPage={onPage} onRange={onRange} /></RepositoryAccess.Provider></QueryClientProvider>);
    return { client, onPage, onRange, revoke };
}
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); vi.restoreAllMocks(); highlight.fail = false; });

it("opens highlighted source by default with the pinned range and compact version metadata", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue(page);
    mount();
    const code = await screen.findByLabelText("Подсвеченный код src/example.py");
    expect(code.getAttribute("data-range")).toBe("55-56");
    expect(code.textContent).toBe("😀Привет\r\nprint('hello')\r\n");
    expect(screen.getByRole("button", { name: "Обычный текст" }).getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByText("Python")).toBeTruthy();
    expect(screen.getByText("строки 55–56")).toBeTruthy();
    expect(screen.getByText(sha.slice(0, 7)).title).toBe(sha);
    expect(screen.getByRole("link", { name: "Открыть в GitHub" }).getAttribute("rel")).toContain("noopener");
    expect(get).toHaveBeenCalledWith(expect.stringContaining("max_chars=8000&line_start=55"), expect.objectContaining({ signal: expect.any(AbortSignal) }));
});

it("offers numbered plain text, shift ranges, Unicode paging and an exact copy of the fragment", async () => {
    vi.spyOn(api, "get").mockResolvedValue(page);
    const { onPage, onRange } = mount();
    await screen.findByLabelText("Подсвеченный код src/example.py");
    fireEvent.click(screen.getByRole("button", { name: "Обычный текст" }));
    const code = screen.getByLabelText("Код src/example.py");
    expect(code.textContent).not.toContain("\r");
    expect(screen.queryByRole("button", { name: "Выбрать строку 57" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Выбрать строку 56" }), { shiftKey: true });
    expect(onRange).toHaveBeenLastCalledWith({ line: 55, end: 56 });
    fireEvent.click(screen.getByRole("button", { name: "Выбрать строку 56" }));
    expect(onRange).toHaveBeenLastCalledWith({ line: 56, end: 56 });
    fireEvent.click(screen.getByRole("button", { name: "Читать дальше" }));
    expect(onPage).toHaveBeenCalledWith(page.offset + Array.from("😀Привет\r\nprint('hello')\r\n").length);
    fireEvent.click(screen.getByRole("button", { name: "Копировать фрагмент" }));
    const { copyText } = await import("@/lib/clipboard");
    expect(copyText).toHaveBeenCalledWith("😀Привет\r\nprint('hello')\r\n");
    await screen.findByText("Скопировано");
});

it("keeps readable source and range actions when highlighting cannot load", async () => {
    highlight.fail = true;
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(api, "get").mockResolvedValue({ ...page, source_url: "javascript:alert(1)" });
    mount();
    await screen.findByText("Подсветка недоступна. Показан обычный текст.");
    expect(screen.getByLabelText("Код src/example.py").textContent).toContain("😀Привет");
    expect(screen.getByRole("button", { name: "Выбрать строку 55" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Открыть в GitHub" })).toBeNull();
});

it("removes rendered source after access is revoked while preserving the access callback", async () => {
    let denied = false;
    vi.spyOn(api, "get").mockImplementation(async () => { if (denied) throw new ApiError(403, "Доступ отозван"); return page; });
    const { client, revoke } = mount();
    await screen.findByLabelText("Подсвеченный код src/example.py");
    denied = true;
    await client.invalidateQueries({ queryKey: repositoryKey(7, 2) });
    await screen.findByText("Доступ отозван");
    await waitFor(() => expect(screen.queryByLabelText("Подсвеченный код src/example.py")).toBeNull());
    expect(revoke).toHaveBeenCalledWith(2);
    expect(screen.queryByRole("button", { name: "Копировать фрагмент" })).toBeNull();
});

it("keeps the reading anchor when selecting visible lines but fetches an externally selected symbol", async () => {
    const get = vi.spyOn(api, "get").mockImplementation(async path => {
        const line = Number(new URL(path, "http://local").searchParams.get("line_start") ?? 150);
        return { ...page, line_start: line, line_end: line + 1 };
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    clients.push(client);
    function Parent() {
        const [selection, setSelection] = useState(source);
        const [pending, setPending] = useState({ line: source.line, end: source.end });
        return <><SourceViewer project={7} selection={selection} onPage={offset => setSelection(current => ({ ...current, offset }))} onRange={setPending} /><button onClick={() => setSelection(current => ({ ...current, ...pending }))}>Apply permalink</button><button onClick={() => setSelection(current => ({ ...current, line: 200, end: 201 }))}>External symbol</button></>;
    }
    render(<QueryClientProvider client={client}><Parent /></QueryClientProvider>);
    await screen.findByLabelText("Подсвеченный код src/example.py");
    fireEvent.click(screen.getByRole("button", { name: "Обычный текст" }));
    fireEvent.click(screen.getByRole("button", { name: "Выбрать строку 56" }));
    // A router may apply the URL in a transition, after the viewer has already rerendered.
    fireEvent.click(screen.getByRole("button", { name: "Apply permalink" }));
    expect(screen.getByRole("button", { name: "Выбрать строку 55" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Выбрать строку 56" }).getAttribute("aria-pressed")).toBe("true");
    expect(get).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "External symbol" }));
    await screen.findByRole("button", { name: "Выбрать строку 200" });
    expect(get).toHaveBeenCalledTimes(2);
    expect(get).toHaveBeenLastCalledWith(expect.stringContaining("line_start=200"), expect.anything());
    fireEvent.click(screen.getByRole("button", { name: "Читать дальше" }));
    await screen.findByRole("button", { name: "Выбрать строку 151" });
    fireEvent.click(screen.getByRole("button", { name: "Выбрать строку 151" }));
    fireEvent.click(screen.getByRole("button", { name: "Apply permalink" }));
    expect(get).toHaveBeenCalledTimes(3);
    fireEvent.click(screen.getByRole("button", { name: "К выбранным строкам" }));
    await waitFor(() => expect(get).toHaveBeenCalledTimes(4));
    expect(get).toHaveBeenLastCalledWith(expect.stringContaining("line_start=151"), expect.anything());
});
