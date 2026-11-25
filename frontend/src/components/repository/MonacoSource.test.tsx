// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import MonacoSource from "./MonacoSource";

const editor = vi.hoisted(() => ({
    create: vi.fn(), createDecorationsCollection: vi.fn(), clear: vi.fn(), revealLineInCenterIfOutsideViewport: vi.fn(),
    dispose: vi.fn(), modelDispose: vi.fn(), onMouseDown: vi.fn(), clickDispose: vi.fn(),
}));
vi.mock("monaco-editor/editor/editor.api.js", () => ({
    editor: { create: editor.create, MouseTargetType: { GUTTER_LINE_NUMBERS: 2 } },
    Range: class {
        startLineNumber: number; startColumn: number; endLineNumber: number; endColumn: number;
        constructor(startLineNumber: number, startColumn: number, endLineNumber: number, endColumn: number) {
            this.startLineNumber = startLineNumber; this.startColumn = startColumn; this.endLineNumber = endLineNumber; this.endColumn = endColumn;
        }
    },
}));
vi.mock("monaco-editor/editor/editor.worker.js?worker", () => ({ default: class {} }));
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
editor.create.mockImplementation(() => ({
    createDecorationsCollection: editor.createDecorationsCollection.mockReturnValue({ clear: editor.clear }),
    revealLineInCenterIfOutsideViewport: editor.revealLineInCenterIfOutsideViewport,
    dispose: editor.dispose, getModel: () => ({ dispose: editor.modelDispose }),
    onMouseDown: editor.onMouseDown.mockReturnValue({ dispose: editor.clickDispose }),
}));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it("creates a compact read-only syntax viewer with absolute line numbers and horizontal scrolling", () => {
    const { container, unmount } = render(<MonacoSource text={"const name = '😀';\r\nexport { name };\r\n"} path="src/example.ts" firstLine={55} line={55} end={90} />);
    const options = editor.create.mock.calls[0][1];
    expect(options).toMatchObject({ language: "typescript", readOnly: true, domReadOnly: true, wordWrap: "off", scrollBeyondLastLine: false, ariaLabel: "Код src/example.ts", value: "const name = '😀';\r\nexport { name };" });
    expect(options.lineNumbers(2)).toBe("56");
    expect((container.firstElementChild as HTMLElement).style.height).toBe("104px");
    expect(editor.createDecorationsCollection).toHaveBeenCalledWith([expect.objectContaining({ range: expect.objectContaining({ startLineNumber: 1, endLineNumber: 2 }) })]);
    unmount();
    expect(editor.clickDispose).toHaveBeenCalledOnce();
    expect(editor.dispose).toHaveBeenCalledOnce();
    expect(editor.modelDispose).toHaveBeenCalledOnce();
});

it("updates range decorations without recreating the model and supports shift-click on the gutter", () => {
    const onRange = vi.fn();
    const props = { text: "one\ntwo\nthree", path: "test.py", firstLine: 55, onRange };
    const { rerender } = render(<MonacoSource {...props} line={55} end={55} />);
    rerender(<MonacoSource {...props} line={56} end={57} />);
    expect(editor.create).toHaveBeenCalledOnce();
    expect(editor.createDecorationsCollection).toHaveBeenLastCalledWith([expect.objectContaining({ range: expect.objectContaining({ startLineNumber: 2, endLineNumber: 3 }) })]);
    const click = editor.onMouseDown.mock.calls[0][0];
    click({ target: { type: 2, position: { lineNumber: 3 } }, event: { shiftKey: true } });
    expect(onRange).toHaveBeenLastCalledWith({ line: 56, end: 57 });
    click({ target: { type: 2, position: { lineNumber: 1 } }, event: { shiftKey: false } });
    expect(onRange).toHaveBeenLastCalledWith({ line: 55, end: 55 });
});

it("does not highlight an unrelated paged fragment and caps a long file viewport", () => {
    const { container } = render(<MonacoSource text={Array(100).fill("line").join("\n")} path="data.yaml" firstLine={150} line={5} end={10} />);
    expect(editor.create.mock.calls[0][1].language).toBe("yaml");
    expect(editor.createDecorationsCollection).toHaveBeenCalledWith([]);
    expect(editor.revealLineInCenterIfOutsideViewport).not.toHaveBeenCalled();
    expect((container.firstElementChild as HTMLElement).style.height).toBe("560px");
});
