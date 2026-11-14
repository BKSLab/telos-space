"""Проверенный фрагмент конкретного текстового представления документа."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DocumentFragmentSchema(BaseModel):
    """Координаты относятся к raw Markdown/извлечённому тексту, не HTML или RAG."""

    model_config = ConfigDict(extra="forbid")

    document_ref: str
    rendition: Literal["markdown", "extracted_text"]
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    offset_unit: Literal["unicode_codepoints"] = "unicode_codepoints"
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_range(self) -> "DocumentFragmentSchema":
        """Python len считает Unicode codepoints, как объявлено в контракте."""
        if self.end <= self.start or self.end - self.start != len(self.quote):
            raise ValueError("Диапазон фрагмента не соответствует длине цитаты.")
        return self
