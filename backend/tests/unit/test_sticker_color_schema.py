"""Цвет стикера — свободный hex, как у стадии канбана (миграция 0008)."""

import pytest
from pydantic import ValidationError

from src.schemas.board_shapes import BoardShapeToStickerSchema
from src.schemas.board_stickers import (
    BoardStickerCreateSchema,
    BoardStickerUpdateSchema,
)


def test_create_defaults_to_yellow_hex() -> None:
    assert BoardStickerCreateSchema(body="Заметка").color == "#fff19a"


@pytest.mark.parametrize("value", ["#7C1623", "#7c1623", " #7C1623 "])
def test_hex_is_stored_lowercase(value: str) -> None:
    assert BoardStickerCreateSchema(body="x", color=value).color == "#7c1623"


def test_palette_names_are_rejected() -> None:
    """Все операции принимают один hex-формат без алиасов палитры."""
    for name in ("yellow", "Blue", "violet", "neutral"):
        with pytest.raises(ValidationError):
            BoardStickerCreateSchema(body="x", color=name)
        with pytest.raises(ValidationError):
            BoardStickerUpdateSchema(revision=1, color=name)
        with pytest.raises(ValidationError):
            BoardShapeToStickerSchema(color=name)


@pytest.mark.parametrize("value", ["#fff", "fff7bf", "#fff19a00", "#ggg000", "orange", ""])
def test_not_a_six_digit_hex_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        BoardStickerCreateSchema(body="x", color=value)
    with pytest.raises(ValidationError):
        BoardShapeToStickerSchema(color=value)


def test_update_rejects_explicit_null_color() -> None:
    with pytest.raises(ValidationError):
        BoardStickerUpdateSchema(revision=1, color=None)
