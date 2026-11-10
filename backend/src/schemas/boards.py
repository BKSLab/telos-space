from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.schemas.board_shapes import BoardShapeSchema
from src.schemas.board_stickers import BoardStickerSchema

MAX_BOARD_TITLE_LENGTH = 120


def _normalize_title(value: str) -> str:
    """Убирает внешние пробелы и запрещает визуально пустое название."""
    normalized = value.strip()
    if not normalized:
        raise ValueError("Название доски не может быть пустым.")
    return normalized


class BoardCreateSchema(BaseModel):
    """Новая доска проекта; встаёт последней вкладкой."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=MAX_BOARD_TITLE_LENGTH)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        """Нормализует название доски."""
        return _normalize_title(value)


class BoardUpdateSchema(BaseModel):
    """Переименование или перестановка доски с ожидаемой ревизией."""

    model_config = ConfigDict(extra="forbid")

    revision: int = Field(..., ge=1)
    title: str | None = Field(None, min_length=1, max_length=MAX_BOARD_TITLE_LENGTH)
    position: int | None = Field(None, ge=0, le=1_000_000)

    @model_validator(mode="after")
    def validate_changes(self) -> Self:
        """Требует хотя бы одно изменение и не принимает явный null."""
        if not self.model_fields_set - {"revision"}:
            raise ValueError("Нужно передать изменение доски.")
        if "title" in self.model_fields_set:
            if self.title is None:
                raise ValueError("Название доски не может быть NULL.")
            self.title = _normalize_title(self.title)
        if "position" in self.model_fields_set and self.position is None:
            raise ValueError("Позиция доски не может быть NULL.")
        return self


class BoardSchema(BaseModel):
    """Доска проекта для вкладок над холстом."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int | None
    title: str
    position: int
    revision: int
    created_by_user_id: int | None
    created_at: datetime
    updated_at: datetime


class BoardContentSchema(BaseModel):
    """Всё содержимое доски одним ответом."""

    board: BoardSchema
    stickers: list[BoardStickerSchema]
    shapes: list[BoardShapeSchema]
