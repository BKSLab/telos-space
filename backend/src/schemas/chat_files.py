"""Безопасный контракт чтения файлов текущей переписки Ариса."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

ChatFileStatus = Literal[
    "pending", "processing", "ready", "empty", "failed", "unsupported", "disabled"
]


class ChatFileSchema(BaseModel):
    """Оригинал, отправленный в доступной части текущей переписки."""

    file_id: UUID = Field(
        description="UUID оригинала в текущей переписке; не путь файла.",
        examples=["8d06b2c4-d25b-4c2b-8a0c-7a274a7247cb"],
    )
    original_name: str = Field(
        description="Исходное имя, выбранное пользователем.", examples=["Решения.md"]
    )
    content_type: str = Field(
        description="Серверный MIME-тип оригинала.", examples=["text/markdown"]
    )
    size: int = Field(description="Размер неизменяемого оригинала в байтах.", examples=[2048])
    message_id: int = Field(
        description="Отправленное сообщение, которому принадлежит файл.", examples=[42]
    )
    created_at: datetime = Field(
        description="Время загрузки оригинала в UTC.", examples=["2026-11-14T10:00:00Z"]
    )
    status: ChatFileStatus = Field(
        description="Подготовка текста: pending — очередь; processing — извлечение; ready — готов; empty — нет текста; failed — ошибка; unsupported — формат не поддержан; disabled — vision выключен.",
        examples=["ready"],
    )
    detail: str | None = Field(
        default=None,
        description="Безопасное пояснение состояния или ресурсного ограничения.",
        examples=["Распознавание изображений выключено."],
    )
    original_chars: int = Field(
        default=0,
        description="Число Unicode codepoints в результате извлечения до ограничения размера кэша; 0 до подготовки.",
        examples=[1200000],
    )
    truncated: bool = Field(
        default=False,
        description="True, если ресурсный предел ограничил кэш. Такой текст и результаты поиска нельзя считать полным содержимым оригинала.",
        examples=[False],
    )
    content_hash: str | None = Field(
        default=None,
        description="SHA256 всего доступного текста кэша в UTF-8; не hash оригинальных байтов и не отдельной страницы.",
        examples=["a" * 64],
    )
    total_chars: int = Field(
        default=0,
        description="Доступное число Unicode codepoints в кэше; может быть меньше original_chars при truncated=true.",
        examples=[1000000],
    )
    excerpt: str | None = Field(
        default=None,
        description="Начальные не более 600 Unicode codepoints готового кэша для списка; полное чтение выполняет отдельный bounded read.",
        examples=["Принятые решения по проекту…"],
    )


class ChatFileListSchema(BaseModel):
    """Страница оригиналов без путей локального хранилища."""

    items: list[ChatFileSchema] = Field(
        description="Файлы от текущего вопроса к ранней истории, без черновиков и будущих/удалённых сообщений.",
        examples=[[]],
    )
    total: int = Field(
        description="Число доступных файлов после фильтра имени в текущем контексте.", examples=[3]
    )
    limit: int = Field(
        description="Запрошенный размер страницы метаданных, от 1 до 50.", examples=[20]
    )
    offset: int = Field(description="Смещение страницы метаданных.", examples=[0])
    next_offset: int | None = Field(
        description="Смещение следующей страницы; null, если файлов больше нет.",
        examples=[20, None],
    )


class ChatFileTextSchema(ChatFileSchema):
    """Ограниченная страница извлечённого текста; hash относится ко всему кэшу."""

    text: str = Field(
        default="",
        description="Страница доступного кэша не длиннее max_chars (1–8000); пустая строка при неготовом тексте или отсутствии совпадения.",
        examples=["Принятые решения"],
    )
    offset: int = Field(
        ge=0,
        description="Абсолютное начало возвращённой страницы в Unicode codepoints исходного текста кэша, без UTF-16/байтовых смещений.",
        examples=[0],
    )
    next_offset: int | None = Field(
        default=None,
        description="Продолжение чтения или буквального поиска; при поиске может указывать на следующее совпадение внутри прежнего окна. null — продолжения нет.",
        examples=[8000, None],
    )
    offset_unit: Literal["unicode_codepoints"] = Field(
        default="unicode_codepoints",
        description="Единица всех смещений и длин текста; один astral Unicode символ считается одним codepoint.",
        examples=["unicode_codepoints"],
    )
    matches: list["ChatFileMatchSchema"] = Field(
        default_factory=list,
        description="Не более 20 абсолютных интервалов буквального case-insensitive поиска q в возвращённом окне; конец интервала исключительный.",
        examples=[[{"start": 20, "end": 27}]],
    )
    total_matches: int | None = Field(
        default=None,
        description="Число совпадений во всём доступном кэше при поиске q; null при обычном чтении. При truncated=true не характеризует весь оригинал.",
        examples=[3, None],
    )


class ChatFileMatchSchema(BaseModel):
    """Абсолютное положение буквального совпадения в доступном тексте."""

    start: int = Field(
        description="Включительное абсолютное начало совпадения в Unicode codepoints.",
        examples=[20],
    )
    end: int = Field(
        description="Исключительный абсолютный конец совпадения в Unicode codepoints.",
        examples=[27],
    )


class ChatFilePreparationSchema(BaseModel):
    """Статус собственной загрузки composer без доступа к извлечённому тексту."""

    file_id: UUID = Field(
        description="UUID собственной загрузки composer.",
        examples=["8d06b2c4-d25b-4c2b-8a0c-7a274a7247cb"],
    )
    status: ChatFileStatus = Field(
        description="Состояние подготовки: pending, processing, ready, empty, failed, unsupported или disabled; черновик недоступен Арису независимо от готовности.",
        examples=["processing"],
    )
    detail: str | None = Field(
        default=None,
        description="Безопасное пояснение ошибки или ограничения подготовки.",
        examples=["Текст не найден."],
    )
    total_chars: int = Field(
        default=0,
        description="Доступная длина кэша в Unicode codepoints, без раскрытия текста черновика.",
        examples=[8000],
    )
    original_chars: int = Field(
        default=0,
        description="Длина извлечённого текста до ресурсного ограничения в Unicode codepoints.",
        examples=[12000],
    )
    truncated: bool = Field(
        default=False,
        description="Кэш ограничен серверным ресурсным пределом и не представляет полный текст.",
        examples=[False],
    )
