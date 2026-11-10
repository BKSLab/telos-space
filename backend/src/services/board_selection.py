"""Выделение доски — проверенные ссылки, без расширения прав участника."""

from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError

from src.schemas.agent_conversations import MAX_BOARD_SELECTION, BoardSelectionRefSchema


def normalize_board_selection(raw: object) -> list[dict[str, Any]]:
    """Отбрасывает некорректные ссылки и дубли, сохраняя порядок и тип объекта."""
    if not isinstance(raw, list) or len(raw) > MAX_BOARD_SELECTION:
        return []
    refs = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            ref = BoardSelectionRefSchema.model_validate(item)
        except ValidationError:
            continue
        key = (ref.type, ref.id)
        if key not in seen:
            seen.add(key)
            refs.append(ref.model_dump())
    return refs


def checked_board_selection(
    raw: object,
    *,
    board_id: int,
    shapes: Iterable[Any],
    stickers: Iterable[Any],
) -> list[dict[str, Any]]:
    """Оставляет ссылки только на объекты уже проверенной доступной доски."""
    available = {
        (kind, item.id)
        for kind, objects in (("shape", shapes), ("sticker", stickers))
        for item in objects
        if item.board_id == board_id
    }
    return [ref for ref in normalize_board_selection(raw) if (ref["type"], ref["id"]) in available]
