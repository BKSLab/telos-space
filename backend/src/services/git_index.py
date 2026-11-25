"""Статическая, ограниченная обработка Git; код и ссылки никогда не исполняются."""

import hashlib
import json
import re
from bisect import bisect_right
from pathlib import PurePosixPath

from src.services.git_structure import extract_structure

POLICY_VERSION = 3


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
    """Возвращает полный статический анализ; relations не доказывают runtime-поведение."""
    suffix = PurePosixPath(path).suffix.lower()
    result = extract_structure(path, text)
    result["kind"] = "text"
    if suffix in {".py", ".pyi"}:
        result["kind"] = "python_module"
    elif suffix in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"}:
        result["kind"] = "javascript_module"
    elif suffix in {".md", ".mdx", ".rst", ".txt", ".adoc"}:
        result["kind"] = "documentation"
    elif suffix in {".json", ".toml", ".yaml", ".yml", ".ini", ".cfg"}:
        result["kind"] = "configuration"
    return result


def text_fragments(
    text: str, *, max_chars: int = 8000, path: str | None = None,
    structure: dict | None = None,
) -> list[dict]:
    """Делит оригинал по декларациям/секциям, затем строкам; контекст хранит отдельно."""
    if max_chars < 500:
        raise ValueError("Фрагмент слишком мал.")
    newlines = [index for index, char in enumerate(text) if char == "\n"]
    result = []
    start = 0
    symbols = (structure or {}).get("symbols", [])
    if structure is None and path:
        symbols = extract_structure(path, text)["symbols"]
    declarations = [symbol for symbol in symbols if symbol["id"] != "$file"]
    boundaries = {symbol[key] for symbol in declarations for key in ("offset", "end_offset")}
    # Sections are evidence from syntax only, including SQL statements and config tables.
    for match in re.finditer(r'(?m)^(?:#{1,6}\s|\[[^\r\n]+\]|[A-Za-z_][\w.-]*\s*[:=]|\s{0,2}"[^"\r\n]+"\s*:|(?:CREATE|ALTER|INSERT|SELECT|WITH)\b)', text):
        boundaries.add(match.start())
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            structural = [boundary for boundary in boundaries if start < boundary <= end]
            # Avoid cutting a small declaration that starts within this window.
            crossing = [symbol["offset"] for symbol in declarations if
                        start < symbol["offset"] < end < symbol["end_offset"] and
                        symbol["end_offset"] - symbol["offset"] <= max_chars]
            if crossing:
                end = min(crossing)
            elif structural:
                end = max(structural)
            else:
                boundary = text.rfind("\n", start + max_chars // 2, end)
                if boundary >= 0:
                    end = boundary + 1
        owners = [symbol for symbol in declarations if symbol["offset"] <= start < end <= symbol["end_offset"]]
        owner = min(owners, key=lambda symbol: symbol["end_offset"] - symbol["offset"], default=None)
        result.append(
            {
                "ordinal": len(result),
                "offset": start,
                "end_offset": end,
                "line_start": bisect_right(newlines, start - 1) + 1,
                "line_end": bisect_right(newlines, end - 1) + 1,
                "text": text[start:end],
                "symbol_id": owner["id"] if owner else None,
                "context": {"qualified_name": owner["qualified_name"], "signature": owner["signature"]} if owner else None,
            }
        )
        if end == len(text):
            break
        start = end
    return result
