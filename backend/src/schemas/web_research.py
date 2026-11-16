"""Публичные DTO исследования: разрешение, поисковые кандидаты, прочитанный текст."""

from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from src.schemas.web_context import WebReadResult, WebSearchRequest, WebSource


class WebTopicApprovalSchema(WebSearchRequest):
    """Точный запрос человека; вызывается из UI или после решения pending."""


class WebUrlApprovalSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=4096, description="Точный публичный URL человека.")


class WebTopicGrantSchema(BaseModel):
    public_topic: str = Field(description="Тема, которую пользователь разрешил отправить наружу.")
    topic_token: str = Field(description="Подпись темы; не credential поискового провайдера.")
    expires_at: AwareDatetime = Field(description="После этого срока нужно новое разрешение.")


class WebPageGrantSchema(BaseModel):
    url: str = Field(description="Разрешённый адрес, не произвольный аргумент модели.")
    page_token: str = Field(description="Подпись адреса, проекта и текущего пользователя.")
    expires_at: AwareDatetime = Field(description="Граница действия разрешения страницы.")


class WebSearchSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic_token: str = Field(
        min_length=1, max_length=8192, description="Разрешение публичной темы."
    )
    limit: int = Field(default=5, ge=1, le=10, description="Предел поисковых кандидатов.")


class WebReadSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_token: str = Field(min_length=1, max_length=8192, description="Разрешённая страница.")
    max_chars: int = Field(default=8000, ge=1, le=20000, description="Предел текстового фрагмента.")


class WebSaveSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID = Field(description="Квитанция явного сохранения; повтор не создаёт копию.")
    page_token: str = Field(
        min_length=1, max_length=8192, description="Разрешение прочитанной страницы."
    )
    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$", description="Хэш прочитанной версии.")
    title: str = Field(
        min_length=1, max_length=200, description="Название сохраняемого исследования."
    )


class WebSearchPageSchema(WebSource):
    page_token: str = Field(description="Подписанный маршрут чтения этого поискового кандидата.")


class WebResearchSearchSchema(BaseModel):
    provider: str = Field(default="brave", description="Настроенный поисковый провайдер.")
    fetched_at: AwareDatetime = Field(description="Время получения поискового ответа.")
    items: list[WebSearchPageSchema] = Field(description="Кандидаты; страницы ещё не прочитаны.")


class WebResearchReadSchema(WebReadResult):
    page_token: str = Field(
        description="Разрешение исходного адреса для повторного чтения/сохранения."
    )


class WebResearchStatusSchema(BaseModel):
    reading_enabled: bool = Field(description="Разрешено чтение публичных страниц после согласия.")
    search_enabled: bool = Field(description="Настроен ключ поиска; сам ключ не возвращается.")
    provider: str = Field(default="brave", description="Первый поддерживаемый провайдер.")
