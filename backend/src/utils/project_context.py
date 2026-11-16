"""Компактная проекция единственного паспорта без догадок из Markdown."""

from collections.abc import Mapping
from typing import Any

from src.utils.project_description import DESCRIPTION_BLOCKS

PROJECT_CONTEXT_PREVIEW_CHARS = 160
PROJECT_CONTEXT_FIELD_NAMES = tuple(name for name, _ in DESCRIPTION_BLOCKS)


def project_context_values(
    sections: Mapping[str, Any] | None,
    description_md: str | None,
) -> dict[str, str]:
    """Возвращает известные поля; прежний свободный текст остаётся additional.

    Args:
        sections: Сохранённые структурированные поля либо отсутствие структуры.
        description_md: Свободный текст прежнего проекта, без разбора заголовков.
    Returns:
        Полный набор полей, включая явно пустые; значения не выводятся из текста.
    """
    result = {name: "" for name in PROJECT_CONTEXT_FIELD_NAMES}
    if sections is None:
        result["additional"] = (description_md or "").strip()
    else:
        for name in result:
            value = sections.get(name, "")
            result[name] = value.strip() if isinstance(value, str) else ""
    return result


def compact_project_context(
    *,
    sections: Mapping[str, Any] | None,
    description_md: str | None,
    revision: int,
) -> dict[str, Any]:
    """Строит ограниченное превью, честно отделяя пропуски и сокращения.

    Args:
        sections: Единые поля паспорта.
        description_md: Неинтерпретируемое прежнее описание.
        revision: Текущая серверная версия.
    Returns:
        Версия, заполненные превью до 160 Unicode-символов, пробелы и сокращения.
    """
    values = project_context_values(sections, description_md)
    return {
        "revision": revision,
        "fields": {
            name: value[:PROJECT_CONTEXT_PREVIEW_CHARS] for name, value in values.items() if value
        },
        "unknown_fields": [name for name, value in values.items() if not value],
        "truncated_fields": [
            name for name, value in values.items() if len(value) > PROJECT_CONTEXT_PREVIEW_CHARS
        ],
    }
