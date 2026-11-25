import { useEffect, useState } from "react";
import * as monaco from "monaco-editor/editor/editor.api.js";
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
import { StaticSource, type SourceToken, type StaticSourceProps } from "./StaticSource";

// Tokenization does not create an editor, model, language server or worker.
jsonDefaults.setModeConfiguration({ tokens: true, completionItems: false, hovers: false, documentSymbols: false, documentFormattingEdits: false, documentRangeFormattingEdits: false, colors: false, foldingRanges: false, diagnostics: false, selectionRanges: false });

export default function MonacoSource(props: Omit<StaticSourceProps, "tokens">) {
    const { text, path } = props;
    const [highlight, setHighlight] = useState<{ text: string; path: string; tokens: SourceToken[][] } | null>(null);
    const [failed, setFailed] = useState(false);
    useEffect(() => {
        let active = true;
        const extension = path.split(".").pop()?.toLowerCase() ?? "";
        const language = ({ py: "python", ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript", md: "markdown", json: "json", css: "css", html: "html", yml: "yaml", yaml: "yaml", sh: "shell", sql: "sql" } as Record<string, string>)[extension] ?? "plaintext";
        // Monaco also splits bare CR. Preserve API line numbers and UTF-16 token
        // offsets by replacing only those separators in its tokenization input.
        const tokenText = text.replace(/\r(?!\n)/g, " ");
        // colorize awaits Monaco's lazy grammar registration (tokenize is sync).
        // Its HTML result is discarded: React renders only original text slices.
        void monaco.editor.colorize(tokenText, language, {}).then(() => {
            if (active) { setHighlight({ text, path, tokens: monaco.editor.tokenize(tokenText, language) }); setFailed(false); }
        }).catch(() => { if (active) setFailed(true); });
        return () => { active = false; };
    }, [text, path]);
    return <>{failed && <p role="status" className="repository-source-notice">Подсветка недоступна. Показан обычный текст.</p>}<StaticSource {...props} tokens={highlight?.text === text && highlight.path === path ? highlight.tokens : undefined} /></>;
}
