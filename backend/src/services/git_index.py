"""Статическая, ограниченная обработка Git; код и ссылки никогда не исполняются."""

import ast
import hashlib
import json
import re
from bisect import bisect_right
from fnmatch import fnmatchcase
from pathlib import PurePosixPath

POLICY_VERSION = 1
EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".svn",
        "node_modules",
        "vendor",
        "venv",
        ".venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
        "coverage",
        ".next",
        ".nuxt",
        ".cache",
        ".idea",
        "uploads",
    }
)
SECRET_NAMES = frozenset(
    {
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".git-credentials",
        "id_rsa",
        "id_dsa",
        "id_ed25519",
        "credentials",
        "credentials.json",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
    }
)
TEXT_EXTENSIONS = frozenset(
    {
        ".md",
        ".mdx",
        ".txt",
        ".rst",
        ".adoc",
        ".py",
        ".pyi",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".json",
        ".toml",
        ".yaml",
        ".yml",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cs",
        ".css",
        ".scss",
        ".html",
        ".sql",
        ".sh",
        ".ps1",
        ".ini",
        ".cfg",
        ".xml",
        ".vue",
        ".svelte",
        ".rb",
        ".php",
        ".proto",
        ".graphql",
    }
)
TEXT_NAMES = frozenset(
    {
        "dockerfile",
        "makefile",
        "readme",
        "license",
        "procfile",
        "gemfile",
        ".gitignore",
        ".dockerignore",
    }
)
TOKEN_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}|\btelos_(?:key|token)_[A-Za-z0-9_]{12,}|\bsk-[A-Za-z0-9_-]{20,}"
)
ASSIGNMENT_PATTERN = re.compile(
    r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]{0,79})\s*[:=]\s*['\"]([^'\"\r\n]{8,})['\"]"
)
SECRET_WORDS = ("PASSWORD", "PASSWD", "SECRET", "TOKEN", "API_KEY", "ACCESS_KEY", "PRIVATE_KEY")
SYMBOL_LIMIT = 200
IMPORT_LIMIT = 100


def policy_hash(
    include_paths: list[str], exclude_patterns: list[str], *, max_file_bytes: int, ref: str = ""
) -> str:
    """Хэширует версию фильтров и разрешённую область, не содержимое credential."""
    value = {
        "version": POLICY_VERSION,
        "ref": ref,
        "include": sorted(set(include_paths)),
        "exclude": sorted(set(exclude_patterns)),
        "max_file_bytes": max_file_bytes,
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def exclusion_reason(
    path: str,
    include_paths: list[str],
    exclude_patterns: list[str],
    *,
    mode: str = "100644",
    size: int = 0,
    max_file_bytes: int = 1024 * 1024,
) -> str | None:
    """Возвращает явную причину пропуска; пользовательские фильтры не снимают обязательные."""
    pure = PurePosixPath(path)
    name = pure.name.lower()
    if mode not in {"100644", "100755"}:
        return "symlink_or_submodule"
    if not any(not root or path == root or path.startswith(root + "/") for root in include_paths):
        return "outside_allowed_roots"
    if any(part.lower() in EXCLUDED_DIRECTORIES for part in pure.parts[:-1]):
        return "generated_or_dependency"
    if (
        name.startswith(".env")
        or name in SECRET_NAMES
        or pure.suffix.lower() in {".key", ".pem", ".p12", ".pfx", ".jks", ".keystore"}
        or "credentials" in name
        or name.startswith("secrets.")
    ):
        return "secret_path"
    if any(
        fnmatchcase(path, pattern) or fnmatchcase(name, pattern) for pattern in exclude_patterns
    ):
        return "excluded_pattern"
    if size > max_file_bytes:
        return "file_too_large"
    if name.endswith((".min.js", ".min.css", ".map", ".lock", "-lock.json")):
        return "generated_or_dependency"
    if (
        pure.suffix.lower() not in TEXT_EXTENSIONS
        and name not in TEXT_NAMES
        and not name.startswith("requirements")
    ):
        return "unsupported_format"
    return None


def has_secret(text: str) -> bool:
    """Консервативно исключает ключи/секретные литералы вместо отправки их модели."""
    if TOKEN_PATTERN.search(text):
        return True
    for match in ASSIGNMENT_PATTERN.finditer(text):
        if any(word in match[1].upper() for word in SECRET_WORDS):
            return True
    return False


def safe_title(text: str) -> str:
    """Upstream подпись не передаёт обнаруженный credential даже в метаданных."""
    return "Скрыто политикой секретов" if has_secret(text) else text[:512]


def prepare_text(content: bytes) -> tuple[str | None, str | None]:
    """UTF-8 original либо явное исключение binary/LFS/секрета."""
    if b"\x00" in content:
        return None, "binary_file"
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None, "unsupported_encoding"
    if text.startswith("version https://git-lfs.github.com/spec/v1"):
        return None, "lfs_pointer"
    if has_secret(text):
        return None, "secret_content"
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
