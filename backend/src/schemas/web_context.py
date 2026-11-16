"""Отдельный контракт публичного веба без проектного или личного контекста."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, field_validator

MAX_WEB_QUERY_CHARS = 600
MAX_WEB_QUERY_WORDS = 75
MAX_WEB_TEXT_CHARS = 20_000
MAX_WEB_RESPONSE_BYTES = 1_048_576


class WebContextConfig(BaseModel):
    """Явная конфигурация клиента; не загружает настройки приложения."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    search_api_key: SecretStr = Field(repr=False, description="Ключ только Brave Search API.")
    deadline_seconds: float = Field(default=20, gt=0, le=20, description="Общий бюджет вызова.")
    io_timeout_seconds: float = Field(
        default=10, gt=0, le=10, description="Бюджет одного обмена или DNS."
    )
    max_response_bytes: int = Field(
        default=MAX_WEB_RESPONSE_BYTES,
        ge=1,
        le=MAX_WEB_RESPONSE_BYTES,
        description="Предел несжатого тела до извлечения текста.",
    )
    max_text_chars: int = Field(
        default=MAX_WEB_TEXT_CHARS,
        ge=1,
        le=MAX_WEB_TEXT_CHARS,
        description="Предел возвращаемого текста в Unicode-символах.",
    )
    max_redirects: int = Field(
        default=3, ge=0, le=3, description="Число вручную проверяемых переходов."
    )


class WebSearchRequest(BaseModel):
    """Публичная тема, подготовленная сервером отдельно от контекста проекта."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(
        min_length=1,
        max_length=MAX_WEB_QUERY_CHARS,
        description="Только одобренная публичная тема.",
        examples=["метод критического пути в управлении проектом"],
    )
    limit: int = Field(default=5, ge=1, le=20, description="Максимальное число результатов.")

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        """Нормализует тему и соблюдает ограничения протокола провайдера."""
        value = value.strip()
        if not value or len(value.split()) > MAX_WEB_QUERY_WORDS:
            raise ValueError("Тема должна содержать от 1 до 75 слов.")
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Управляющие символы в теме запрещены.")
        return value


class WebReadRequest(BaseModel):
    """Адрес публичной страницы; не принимает заголовки, credentials или файлы."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(
        min_length=1,
        max_length=4096,
        description="Публичный HTTP(S)-адрес без учётных данных.",
        examples=["https://www.example.com/article"],
    )
    max_chars: int = Field(
        default=MAX_WEB_TEXT_CHARS,
        ge=1,
        le=MAX_WEB_TEXT_CHARS,
        description="Предел возвращаемого текста страницы.",
    )


class WebSource(BaseModel):
    """Наблюдение внешнего источника; не является фактом знаний проекта."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_kind: Literal["web"] = Field(default="web", description="Внешний публичный источник.")
    source_id: str = Field(
        pattern=r"^web:[0-9a-f]{64}$",
        description="Стабильная идентичность канонического URL, без версии.",
    )
    url: str = Field(description="Канонический конечный адрес источника.")
    title: str = Field(max_length=512, description="Заголовок страницы или результата поиска.")
    text: str = Field(
        max_length=MAX_WEB_TEXT_CHARS,
        description="Прочитанный текст или только поисковый фрагмент.",
    )
    fetched_at: AwareDatetime = Field(description="Время получения этого наблюдения, UTC.")
    content_hash: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="SHA-256 полного извлечённого текста или поискового фрагмента до ограничения ответа.",
    )
    coverage: Literal["search_snippet", "extracted_text", "truncated_text"] = Field(
        description="Поиск не означает чтение; извлечение не означает полное визуальное покрытие HTML."
    )
    total_chars: int = Field(ge=0, description="Размер извлечённого текста до ограничения ответа.")
    reported_date: str | None = Field(
        default=None,
        max_length=128,
        description="Заявленная источником дата (Brave page_age или Last-Modified), не подтверждённая дата публикации.",
    )
    version_hint: str | None = Field(
        default=None,
        max_length=256,
        description="Заявленная источником версия ETag; не заменяет собственный хэш.",
    )


class WebSearchResult(BaseModel):
    """Список внешних кандидатов: каждый элемент остаётся поисковым фрагментом."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["brave"] = Field(default="brave", description="Провайдер поиска.")
    fetched_at: AwareDatetime = Field(description="Время получения поискового ответа, UTC.")
    items: list[WebSource] = Field(
        max_length=20, description="Публичные кандидаты без автоматического чтения страниц."
    )


class WebReadResult(WebSource):
    """Текстовая проекция HTML или plain text после безопасного чтения URL."""

    coverage: Literal["extracted_text", "truncated_text"] = Field(
        description="Покрытие только текстовой проекции полученного ответа."
    )
    requested_url: str = Field(description="Проверенный адрес до HTTP-переходов.")
    media_type: Literal["text/html", "text/plain"] = Field(
        description="Фактическое поддерживаемое представление."
    )
    downloaded_bytes: int = Field(
        ge=0, le=MAX_WEB_RESPONSE_BYTES, description="Размер полученного несжатого тела."
    )
