"""Общий контракт подписи локального оригинала."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_FILE_CAPTION_LENGTH = 4000


def normalize_caption(value: str | None) -> str | None:
    """Нормализует пустую подпись и отвергает запрещённые PostgreSQL символы."""
    if value is None:
        return None
    if "\x00" in value or len(value) > MAX_FILE_CAPTION_LENGTH:
        raise ValueError("Подпись длиннее 4000 символов или содержит недопустимый символ.")
    return value.strip() or None


class FileCaptionUpdateSchema(BaseModel):
    """Явный null удаляет подпись; отсутствие caption считается ошибкой."""

    model_config = ConfigDict(extra="forbid")

    caption: str | None = Field(
        ...,
        max_length=MAX_FILE_CAPTION_LENGTH,
        description="Пользовательская подпись; null удаляет подпись.",
        examples=["Согласованный макет"],
    )

    @field_validator("caption")
    @classmethod
    def validate_caption(cls, value: str | None) -> str | None:
        """Убирает краевые пробелы и нормализует пустое значение."""
        return normalize_caption(value)
