// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import MonacoSource from "./MonacoSource";

const editor = vi.hoisted(() => ({ colorize: vi.fn(), tokenize: vi.fn() }));
vi.mock("monaco-editor/editor/editor.api.js", () => ({ editor }));
vi.mock("monaco-editor/languages/definitions/python/register.js", () => ({}));
vi.mock("monaco-editor/languages/definitions/typescript/register.js", () => ({}));
vi.mock("monaco-editor/languages/definitions/javascript/register.js", () => ({}));
vi.mock("monaco-editor/languages/definitions/markdown/register.js", () => ({}));
vi.mock("monaco-editor/languages/definitions/css/register.js", () => ({}));
vi.mock("monaco-editor/languages/definitions/html/register.js", () => ({}));
vi.mock("monaco-editor/languages/features/json/register.js", () => ({ jsonDefaults: { setModeConfiguration: vi.fn() } }));
vi.mock("monaco-editor/languages/definitions/yaml/register.js", () => ({}));
vi.mock("monaco-editor/languages/definitions/shell/register.js", () => ({}));
vi.mock("monaco-editor/languages/definitions/sql/register.js", () => ({}));

function defaults() {
    editor.colorize.mockResolvedValue('<img src="x" onerror="alert(1)">');
    editor.tokenize.mockImplementation((text: string) => text.split(/\r\n|\r|\n/).map(() => [{ offset: 0, type: "keyword.ts" }, { offset: 5, type: "string.ts" }]));
}
defaults();
afterEach(() => { cleanup(); vi.resetAllMocks(); defaults(); });

it("renders the entire original fragment as native code, with escaped source and no virtual editor", async () => {
    const text = "const name = '😀Привет';\r\n\t<img src=x onerror=alert(1)>\r\n" + Array.from({ length: 150 }, (_, i) => `строка ${i}\n`).join("");
    const { container } = render(<MonacoSource text={text} path="src/example.ts" firstLine={55} line={55} end={56} />);
    const region = screen.getByRole("region", { name: "Код src/example.ts" });
    const code = region.querySelector("pre > code")!;
    expect(code.textContent).toBe(text);
    expect(code.closest('[aria-hidden="true"]')).toBeNull();
    await waitFor(() => expect(region.classList.contains("repository-source-editor")).toBe(true));
    expect(editor.colorize).toHaveBeenCalledWith(text, "typescript", {});
    expect(editor.tokenize).toHaveBeenCalledWith(text, "typescript");
    expect(code.textContent).toBe(text);
    expect(code.querySelector(".repository-source-token-keyword")).toBeTruthy();
    expect(container.querySelectorAll("code")).toHaveLength(1);
    expect(container.querySelector("img, textarea, input, [role=textbox], .monaco-editor")).toBeNull();
    expect(code.lastElementChild?.textContent).toBe("строка 149\n");
});

it("changes selection without retokenizing and supports gutter shift-click", async () => {
    const onRange = vi.fn();
    const props = { text: "one\ntwo\nthree", path: "test.py", firstLine: 55, onRange };
    const { rerender } = render(<MonacoSource {...props} line={55} end={55} />);
    await waitFor(() => expect(editor.tokenize).toHaveBeenCalledOnce());
    rerender(<MonacoSource {...props} line={56} end={57} />);
    expect(editor.tokenize).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: "Выбрать строку 56" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Выбрать строку 57" }), { shiftKey: true });
    expect(onRange).toHaveBeenLastCalledWith({ line: 56, end: 57 });
    fireEvent.click(screen.getByRole("button", { name: "Выбрать строку 55" }));
    expect(onRange).toHaveBeenLastCalledWith({ line: 55, end: 55 });
});

it("keeps complete readable text if tokenization fails", async () => {
    editor.colorize.mockRejectedValue(new Error("Grammar unavailable"));
    const text = "<script>unsafe()</script>\r\n😀\n";
    render(<MonacoSource text={text} path="test.html" firstLine={5} line={1} end={1} />);
    await screen.findByText("Подсветка недоступна. Показан обычный текст.");
    const code = screen.getByRole("region", { name: "Код test.html" }).querySelector("code")!;
    expect(code.textContent).toBe(text);
    expect(code.querySelector("script")).toBeNull();
    expect(code.querySelector(".repository-source-range")).toBeNull();
});

it("ignores an obsolete grammar request when another file is opened", async () => {
    let completeOld!: (value: string) => void;
    editor.colorize.mockImplementationOnce(() => new Promise<string>(resolve => { completeOld = resolve; }));
    const { rerender } = render(<MonacoSource text="old" path="old.py" firstLine={1} line={1} end={1} />);
    rerender(<MonacoSource text="new" path="new.ts" firstLine={1} line={1} end={1} />);
    await waitFor(() => expect(editor.tokenize).toHaveBeenCalledWith("new", "typescript"));
    completeOld("unused");
    await Promise.resolve();
    expect(editor.tokenize).not.toHaveBeenCalledWith("old", "python");
    expect(screen.getByRole("region", { name: "Код new.ts" }).querySelector("code")?.textContent).toBe("new");
});

it("keeps bare CR in displayed text while tokenizer rows follow API line numbers", async () => {
    const { container } = render(<MonacoSource text={"one\rtwo\r\nthree\rfour"} path="test.py" firstLine={1} line={1} end={1} onRange={vi.fn()} />);
    await waitFor(() => expect(editor.tokenize).toHaveBeenCalledWith("one two\r\nthree four", "python"));
    expect(container.querySelector("code")?.textContent).toBe("one\rtwo\r\nthree\rfour");
    expect(screen.getAllByRole("button")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Выбрать строку 3" })).toBeNull();
});
