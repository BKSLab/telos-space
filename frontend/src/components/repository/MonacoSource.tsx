import { useEffect, useRef } from "react";
import * as monaco from "monaco-editor/editor/editor.api.js";
import EditorWorker from "monaco-editor/editor/editor.worker.js?worker";
import "monaco-editor/languages/definitions/python/register.js";
import "monaco-editor/languages/definitions/typescript/register.js";
import "monaco-editor/languages/definitions/javascript/register.js";
import "monaco-editor/languages/definitions/markdown/register.js";
import "monaco-editor/languages/definitions/css/register.js";
import "monaco-editor/languages/definitions/html/register.js";

(self as typeof self & { MonacoEnvironment: { getWorker: () => Worker } }).MonacoEnvironment = { getWorker: () => new EditorWorker() };

export default function MonacoSource({ text, path, firstLine, line, end }: { text: string; path: string; firstLine: number; line: number; end: number }) {
    const root = useRef<HTMLDivElement>(null);
    useEffect(() => {
        if (!root.current) return;
        const extension = path.split(".").pop() ?? "";
        const language = ({ py: "python", ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript", md: "markdown", json: "json", css: "css", html: "html" } as Record<string, string>)[extension] ?? "plaintext";
        const editor = monaco.editor.create(root.current, { value: text, language, theme: "vs-dark", readOnly: true,
            domReadOnly: true, automaticLayout: true, minimap: { enabled: false }, scrollBeyondLastLine: false,
            lineNumbers: value => String(firstLine + value - 1), fontSize: 12, ariaLabel: `РљРѕРґ ${path}`, tabIndex: 0,
        });
        editor.createDecorationsCollection([{ range: new monaco.Range(Math.max(1, line - firstLine + 1), 1, Math.max(1, end - firstLine + 1), 1), options: { isWholeLine: true, className: "repository-source-range" } }]);
        return () => { const model = editor.getModel(); editor.dispose(); model?.dispose(); };
    }, [text, path, firstLine, line, end]);
    return <div ref={root} className="min-h-80 min-w-0 flex-1" />;
}
