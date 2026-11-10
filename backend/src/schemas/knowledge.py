from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.knowledge.catalog import SourceType


class KnowledgeChatMessageSchema(BaseModel):
    """Одна предыдущая реплика для уточняющего вопроса."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        """Не пропускает пустые реплики из одних пробелов."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("Реплика не может быть пустой.")
        return normalized


class KnowledgeAskSchema(BaseModel):
    """Вопрос Project Agent с короткой историей текущего диалога."""

    question: str = Field(min_length=2, max_length=2000)
    history: list[KnowledgeChatMessageSchema] = Field(default_factory=list, max_length=10)

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        """Нормализует вопрос до передачи embeddings API."""
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("Вопрос слишком короткий.")
        return normalized


class KnowledgeSourceCardSchema(BaseModel):
    """Компактная карточка из проверенного снимка проекта, а не из текста LLM."""

    key: str | None = None
    status: str | None = None
    priority: str | None = None
    assignee: str | None = None
    due_date: str | None = None
    summary: str | None = None
    login: str | None = None
    avatar_url: str | None = None
    role: str | None = None


class KnowledgeSourceSchema(BaseModel):
    """Проверяемый источник ответа и данные для навигации в UI."""

    source_id: str
    entity_type: SourceType
    entity_id: int
    title: str
    excerpt: str | None = None
    score: float | None = None
    task_id: int | None = None
    document_slug: str | None = None
    related_source_ids: list[str] = Field(default_factory=list)
    card: KnowledgeSourceCardSchema | None = None


class KnowledgeReadParameters(BaseModel):
    """Аргументы чтения; проект задаётся только сервером."""

    model_config = ConfigDict(extra="forbid")
    entity_type: SourceType | None = None
    source_id: str | None = Field(default=None, max_length=64)
    query: str | None = Field(default=None, max_length=2000)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=5, ge=1, le=30)
    max_chars: int = Field(default=4000, ge=500, le=8000)


class KnowledgeReadRequest(KnowledgeReadParameters):
    """Одинаковое постраничное чтение знаний для чата и MCP."""

    name: Literal["telos_list_sources", "telos_read_source", "telos_list_related_sources", "telos_search_sources"]


class KnowledgeCoverageSchema(BaseModel):
    entity_type: SourceType
    total: int
    indexed: int
    missing: int
    stale: int


class KnowledgeFileIssueSchema(BaseModel):
    source_id: str
    title: str
    status: str
    detail: str | None = None


class KnowledgeAnswerSchema(BaseModel):
    """Ответ агента, основанный только на данных доступного проекта."""

    answer: str
    sources: list[KnowledgeSourceSchema]


class KnowledgeStatusSchema(BaseModel):
    """Наблюдаемое состояние индекса проекта без раскрытия collection name."""

    enabled: bool
    ready: bool
    points_count: int | None
    pending_jobs: int
    processing_jobs: int
    failed_jobs: int
    last_error: str | None
    coverage: list[KnowledgeCoverageSchema] = Field(default_factory=list)
    file_issues: list[KnowledgeFileIssueSchema] = Field(default_factory=list)
    obsolete_points: int = 0


class KnowledgeReindexSchema(BaseModel):
    """Подтверждение постановки полной переиндексации в очередь."""

    queued: bool = True
