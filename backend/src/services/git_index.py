"""Статическая, ограниченная обработка Git; код и ссылки никогда не исполняются."""

import ast
import hashlib
import json
import re
from bisect import bisect_right
from pathlib import PurePosixPath

POLICY_VERSION = 3
SYMBOL_LIMIT = 200
IMPORT_LIMIT = 100


def policy_hash(*, ref: str = "") -> str:
    """Version the all-files catalog and decoding policy, never a path filter."""
    value = {"version": POLICY_VERSION, "ref": ref}
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def prepare_text(content: bytes) -> tuple[str | None, str | None]:
    """Decode text regardless of path/content; classify actual non-text objects."""
    try:
        if content.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
            text = content.decode("utf-32")
        elif content.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = content.decode("utf-16")
        else:
            text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, "binary_file" if b"\x00" in content else "unsupported_encoding"
    if "\x00" in text:
        return None, "binary_file"
    if text.startswith("version https://git-lfs.github.com/spec/v1"):
        return None, "lfs_pointer"
    return text, None


def analyze_file(path: str, text: str) -> dict:
    """Строит только объявленные символы/imports; поведения и продуктовых целей не выдумывает."""
    suffix = PurePosixPath(path).suffix.lower()
    result = {"kind": "text", "symbols": [], "imports": [], "partial": False}
    if suffix in {".py", ".pyi"}:
        result["kind"] = "python_module"
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            return {**result, "partial": True, "reason": "syntax_not_parsed"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                result["symbols"].append(
                    {
                        "name": node.name,
                        "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                        "line": node.lineno,
                    }
                )
            elif isinstance(node, ast.Import):
                result["imports"].extend(
                    {"name": item.name, "line": node.lineno} for item in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                result["imports"].append(
                    {"name": "." * node.level + (node.module or ""), "line": node.lineno}
                )
    elif suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte"}:
        result["kind"] = "javascript_module"
        result["partial"] = True
        result["reason"] = "lexical_only"
        for number, line in enumerate(text.splitlines(), 1):
            symbol = re.search(
                r"\b(?:export\s+)?(?:async\s+)?(?:function|class|interface|type)\s+([A-Za-z_$][A-Za-z0-9_$]*)",
                line,
            )
            if symbol:
                result["symbols"].append({"name": symbol[1], "kind": "candidate", "line": number})
            imported = re.search(
                r"\b(?:from\s*|import\s*|require\(\s*)['\"]([^'\"]{1,256})['\"]", line
            )
            if imported:
                result["imports"].append(
                    {"name": imported[1], "line": number, "confidence": "candidate"}
                )
    elif suffix in {".md", ".mdx", ".rst", ".txt", ".adoc"}:
        result["kind"] = "documentation"
    elif suffix in {".json", ".toml", ".yaml", ".yml", ".ini", ".cfg"}:
        result["kind"] = "configuration"
    if len(result["symbols"]) > SYMBOL_LIMIT or len(result["imports"]) > IMPORT_LIMIT:
        result["partial"] = True
    result["symbols"] = result["symbols"][:SYMBOL_LIMIT]
    result["imports"] = result["imports"][:IMPORT_LIMIT]
    return result


def text_fragments(text: str, *, max_chars: int = 8000) -> list[dict]:
    """Разбивает текст на bounded Unicode fragments с точными исходными offset/lines."""
    if max_chars < 500:
        raise ValueError("Фрагмент слишком мал.")
    newlines = [index for index, char in enumerate(text) if char == "\n"]
    result = []
    start = 0
    overlap = min(400, max_chars // 4)
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            boundary = text.rfind("\n", start + max_chars // 2, end)
            if boundary >= 0:
                end = boundary + 1
        result.append(
            {
                "ordinal": len(result),
                "offset": start,
                "line_start": bisect_right(newlines, start - 1) + 1,
                "line_end": bisect_right(newlines, end - 1) + 1,
                "text": text[start:end],
            }
        )
        if end == len(text):
            break
        start = end - overlap
    return result
