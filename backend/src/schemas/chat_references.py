"""Проверенная цитата сообщения; хранится отдельно от знаний проекта."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatMessageReferenceSchema(BaseModel):
    """Серверный снимок источника и точного диапазона Unicode."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["project_chat", "conversation"] = Field(description="Вид переписки.", examples=["project_chat"])
    chat_id: int = Field(gt=0, description="ID чата или личного диалога в указанном kind.", examples=[1])
    message_id: int = Field(gt=0, description="ID исходного сообщения.", examples=[12])
    project_id: int | None = Field(None, description="Проект источника; null в общем личном диалоге.", examples=[1])
    project_key: str | None = Field(None, description="Текущий ключ проекта для навигации.", examples=["TELOS"])
    author_id: int | None = Field(None, description="Локальный автор; null у Ариса или удалённого участника.", examples=[1])
    author_name: str = Field(description="Имя автора из серверных данных.", examples=["Андрей Иванов"])
    created_at: datetime = Field(description="Время отправки источника.", examples=["2026-11-12T10:00:00Z"])
    revision: int = Field(ge=1, description="Версия сообщения при чтении.", examples=[1])
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$", description="SHA256 полного UTF-8 текста источника.", examples=["a" * 64])
    quote: str = Field(max_length=2000, description="Проверенная дословная цитата; скрыта у недоступного источника.", examples=["Исправлю завтра."])
    start: int = Field(ge=0, description="Начало цитаты в Unicode codepoints.", examples=[0])
    end: int = Field(ge=0, description="Конец цитаты, не включая позицию.", examples=[16])
    offset_unit: Literal["unicode_codepoints"] = Field("unicode_codepoints", description="Единица смещений исходного текста.", examples=["unicode_codepoints"])
    state: Literal["available", "edited", "deleted", "unavailable"] = Field("available", description="Актуальное состояние оригинала при показе ответа.", examples=["available"])
