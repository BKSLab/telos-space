"""Общие настройки и построение запросов полнотекстового поиска."""

import re
from unicodedata import normalize

from sqlalchemy import func, literal_column

HIGHLIGHT_START = "__FTS_START__"
HIGHLIGHT_END = "__FTS_END__"

TITLE_HEADLINE_OPTIONS = (
    f"StartSel={HIGHLIGHT_START}, StopSel={HIGHLIGHT_END}, "
    "MaxWords=512, MinWords=1, ShortWord=1, MaxFragments=0, HighlightAll=true"
)
EXCERPT_HEADLINE_OPTIONS = (
    f"StartSel={HIGHLIGHT_START}, StopSel={HIGHLIGHT_END}, "
    "MaxWords=30, MinWords=8, ShortWord=2, MaxFragments=2, HighlightAll=true"
)

MIN_PREFIX_LENGTH = 3
MAX_PREFIX_TOKENS = 8


def build_ts_query(search: str):
    """Строит websearch-запрос с безопасным префиксным дополнением для простого текста."""
    search_text = search.strip()
    web_query = func.websearch_to_tsquery("russian", search_text)
    prefix_query_text = _build_prefix_query_text(search_text)
    if prefix_query_text is None:
        return web_query
    return web_query.op("||")(func.to_tsquery("russian", prefix_query_text))


def _build_prefix_query_text(search: str) -> str | None:
    """Возвращает безопасное выражение ``token:* & token:*`` без websearch-операторов."""
    if (
        '"' in search
        or re.search(r"(^|\s)-\S", search)
        or re.search(r"\bor\b", search, flags=re.IGNORECASE)
    ):
        return None

    tokens = [
        token
        for token in re.findall(r"[^\W_]+", search.casefold(), flags=re.UNICODE)
        if len(token) >= MIN_PREFIX_LENGTH
    ][:MAX_PREFIX_TOKENS]
    if not tokens:
        return None
    return " & ".join(f"{token}:*" for token in tokens)


def mark_literal_match(value: str, search: str) -> str | None:
    """Размечает первое регистронезависимое совпадение без добавления HTML."""
    start = value.casefold().find(search.casefold())
    if start < 0:
        return None
    end = start + len(search)
    return f"{value[:start]}{HIGHLIGHT_START}{value[start:end]}{HIGHLIGHT_END}{value[end:]}"


def literal_contains(column, term: str):
    """NFKC/регистр/ё как в UI; LIKE метасимволы считаются обычным текстом."""
    normalized = normalize("NFKC", term).lower().replace("ё", "е")
    value = func.replace(func.lower(func.normalize(column, literal_column("NFKC"))), "ё", "е")
    return value.contains(normalized, autoescape=True)
