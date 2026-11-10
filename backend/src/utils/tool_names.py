"""Единое правило имён внешних инструментов TELOS SPACE."""

import re

TOOL_PREFIX = "telos_"
_CANONICAL_TOOL_NAME = re.compile(r"telos_[a-z][a-z0-9]*(?:_[a-z][a-z0-9]*)+")


def require_canonical_tool_name(name: str) -> str:
    """Проверяет имя ``telos_<действие>_<сущность>`` без преобразований.

    Args:
        name: Объявленное или полученное имя инструмента.
    Returns:
        Исходное каноническое имя.
    Raises:
        ValueError: Имя не следует правилу TELOS SPACE.
    """
    if _CANONICAL_TOOL_NAME.fullmatch(name) is None:
        raise ValueError(f"Инструмент {name}: требуется имя telos_<действие>_<сущность> в snake_case.")
    return name
