import { useEffect, useRef } from "react";
import * as monaco from "monaco-editor/editor/editor.api.js";
import EditorWorker from "monaco-editor/editor/editor.worker.js?worker";
import "monaco-editor/languages/definitions/python/register.js";
import "monaco-editor/languages/definitions/typescript/register.js";
import "monaco-editor/languages/definitions/javascript/register.js";
import "monaco-editor/languages/definitions/markdown/register.js";
import "monaco-editor/languages/definitions/css/register.js";
import "monaco-editor/languages/definitions/html/register.js";
import { jsonDefaults } from "monaco-editor/languages/features/json/register.js";
import "monaco-editor/languages/definitions/yaml/register.js";
import "monaco-editor/languages/definitions/shell/register.js";
import "monaco-editor/languages/definitions/sql/register.js";
import type { SourceRange } from "./SourceViewer";

(self as typeof self & { MonacoEnvironment: { getWorker: () => Worker } }).MonacoEnvironment = { getWorker: () => new EditorWorker() };
// Only tokenization is needed for a read-only fragment; no JSON language server or schema requests.
jsonDefaults.setModeConfiguration({ tokens: true, completionItems: false, hovers: false, documentSymbols: false, documentFormattingEdits: false, documentRangeFormattingEdits: false, colors: false, foldingRanges: false, diagnostics: false, selectionRanges: false });

export default function MonacoSource({ text, path, firstLine, line, end, onRange }: {
    text: string; path: string; firstLine: number; line: number; end: number; onRange?: (range: SourceRange) => void;
}) {
    const root = useRef<HTMLDivElement>(null);
    const editorRef = useRef<monaco.editor.IStandaloneCodeEditor | null>(null);
    const selection = useRef({ line, end, onRange });
    useEffect(() => { selection.current = { line, end, onRange }; }, [line, end, onRange]);
    const lineCount = text.replace(/\r?\n$/, "").split(/\r?\n/).length;
    const height = Math.min(560, Math.max(104, lineCount * 21 + 28));
    useEffect(() => {
        if (!root.current) return;
        const extension = path.split(".").pop()?.toLowerCase() ?? "";
        const language = ({ py: "python", ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript", md: "markdown", json: "json", css: "css", html: "html", yml: "yaml", yaml: "yaml", sh: "shell", sql: "sql" } as Record<string, string>)[extension] ?? "plaintext";
        const editor = monaco.editor.create(root.current, { value: text.replace(/\r?\n$/, ""), language, theme: "vs-dark", readOnly: true,
            domReadOnly: true, automaticLayout: true, minimap: { enabled: false }, scrollBeyondLastLine: false,
            lineNumbers: value => String(firstLine + value - 1), lineNumbersMinChars: Math.max(3, String(firstLine + lineCount - 1).length),
            fontSize: 12, lineHeight: 21, fontFamily: getComputedStyle(root.current).getPropertyValue("--font-mono").trim() || "monospace", ariaLabel: `Код ${path}`, tabIndex: 0,
            wordWrap: "off", folding: false, glyphMargin: false, renderLineHighlight: "none", contextmenu: false,
            overviewRulerLanes: 0, hideCursorInOverviewRuler: true, overviewRulerBorder: false,
            padding: { top: 10, bottom: 10 }, scrollbar: { alwaysConsumeMouseWheel: false, horizontalScrollbarSize: 10, verticalScrollbarSize: 10 },
        });
        editorRef.current = editor;
        const click = editor.onMouseDown(event => {
            if (event.target.type !== monaco.editor.MouseTargetType.GUTTER_LINE_NUMBERS || !event.target.position) return;
            const number = firstLine + event.target.position.lineNumber - 1;
            selection.current.onRange?.(event.event.shiftKey ? { line: Math.min(selection.current.line, number), end: Math.max(selection.current.line, number) } : { line: number, end: number });
        });
        return () => { click.dispose(); editorRef.current = null; const model = editor.getModel(); editor.dispose(); model?.dispose(); };
    }, [text, path, firstLine, lineCount]);
    useEffect(() => {
        const editor = editorRef.current;
        if (!editor) return;
        const lastLine = firstLine + lineCount - 1;
        const ranges = end < firstLine || line > lastLine ? [] : [{ range: new monaco.Range(Math.max(1, line - firstLine + 1), 1, Math.min(lineCount, end - firstLine + 1), 1), options: { isWholeLine: true, className: "repository-source-range" } }];
        const decorations = editor.createDecorationsCollection(ranges);
        if (ranges.length) editor.revealLineInCenterIfOutsideViewport(ranges[0].range.startLineNumber);
        return () => decorations.clear();
    }, [text, path, firstLine, lineCount, line, end]);
    return <div ref={root} className="repository-source-editor" style={{ height }} />;
}
