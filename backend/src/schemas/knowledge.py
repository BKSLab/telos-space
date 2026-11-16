from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from src.knowledge.catalog import SourceType
from src.schemas.chat_references import ChatMessageReferenceSchema
from src.schemas.document_references import DocumentFragmentSchema
from src.utils.document_refs import encode_document_ref


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
    """Вопрос Арис с короткой историей текущего диалога."""

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
    document_ref: str | None = None
    reference: DocumentFragmentSchema | None = None
    is_primary: bool = False
    related_source_ids: list[str] = Field(default_factory=list)
    card: KnowledgeSourceCardSchema | None = None

    @model_validator(mode="after")
    def canonical_document_reference(self):
        """Стабильная ссылка — производное серверного identity, включая историю."""
        if self.entity_type in {
            SourceType.DOCUMENT,
            SourceType.ATTACHMENT,
            SourceType.PROJECT_FILE,
        }:
            self.document_ref = encode_document_ref(self.entity_type.value, self.entity_id)
            if self.reference and self.reference.document_ref != self.document_ref:
                raise ValueError("Фрагмент принадлежит другому документу.")
        return self


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

    name: Literal[
        "telos_list_sources",
        "telos_read_source",
        "telos_list_related_sources",
        "telos_search_sources",
    ]


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


class ExternalSourceReferenceSchema(BaseModel):
    """Проверяемые метаданные внешнего наблюдения без credentials и grant tokens."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(description="Серверная идентичность Git-файла или публичного URL.")
    kind: Literal["git_file", "git_change", "web_page"] = Field(
        description="Вид внешнего источника."
    )
    project_id: int = Field(gt=0, description="Проверенный проект источника.")
    title: str = Field(max_length=512, description="Название прочитанного источника.")
    url: str | None = Field(
        default=None, description="Публичная страница либо pinned Git permalink."
    )
    excerpt: str | None = Field(
        default=None,
        max_length=1200,
        description="Фрагмент полученного наблюдения, не полный текст.",
    )
    content_hash: str | None = Field(
        default=None, description="Хэш текста соответствующего наблюдения."
    )
    fetched_at: datetime | None = Field(
        default=None, description="Время получения наблюдения, если известно."
    )
    coverage: Literal[
        "search_snippet", "extracted_text", "truncated_text", "snapshot", "partial_snapshot"
    ] = Field(description="Поисковый фрагмент не объявляется прочитанной страницей.")
    connection_id: int | None = Field(default=None, description="Локальное Git-подключение.")
    task_id: int | None = Field(default=None, description="Задача проверенной Git-связи.")
    change_id: int | None = Field(default=None, description="Проверенный commit/PR.")
    repository_id: int | None = Field(default=None, description="Точная Git repository identity.")
    ref: str | None = Field(default=None, description="Выбранный Git ref.")
    commit_sha: str | None = Field(
        default=None, description="Неизменяемый commit SHA Git-фрагмента."
    )
    path: str | None = Field(default=None, description="POSIX path внутри точного репозитория/SHA.")
    blob_sha: str | None = Field(default=None, description="Git blob SHA.")
    line_start: int | None = Field(
        default=None, ge=1, description="Начальная строка полученного фрагмента."
    )
    line_end: int | None = Field(
        default=None, ge=1, description="Конечная строка полученного фрагмента."
    )
    is_primary: bool = Field(default=False, description="Основной внешний источник ответа.")
    state: Literal["current", "stale", "unavailable"] = Field(
        default="current", description="Доступность/свежесть наблюдения."
    )


class AssertionReferenceSchema(BaseModel):
    """Утверждение карты остаётся отдельным от первичного доказательства."""

    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(gt=0, description="Проект утверждения.")
    claim_id: UUID = Field(description="Стабильный ID утверждения карты.")
    revision: int = Field(ge=1, description="Прочитанная ревизия утверждения.")
    text: str | None = Field(
        default=None, max_length=2400, description="Разрешённый текст прочитанной версии."
    )
    kind: Literal["requirement", "source_fact", "inference", "assumption", "open_question"] = Field(
        description="Происхождение смысла, без превращения вывода в требование."
    )
    verification: Literal[
        "owner_statement",
        "source_statement",
        "participant_report",
        "status_only",
        "analysis",
        "unknown",
    ] = Field(description="Что именно подтверждают первичные материалы.")
    source_ids: list[str] = Field(
        default_factory=list, max_length=30, description="Первичные источники evidence."
    )
    state: Literal["current", "conflicting", "stale", "unavailable"] = Field(
        default="current", description="Состояние проверенной ссылки."
    )
    is_primary: bool = Field(default=False, description="Основное утверждение ответа.")


class KnowledgeAnswerSchema(BaseModel):
    """Ответ агента, основанный только на данных доступного проекта."""

    answer: str
    sources: list[KnowledgeSourceSchema]
    external_sources: list[ExternalSourceReferenceSchema] = Field(
        default_factory=list,
        max_length=20,
        description="Проверенные внешние ссылки без токенов и полного текста.",
    )
    assertion_references: list[AssertionReferenceSchema] = Field(
        default_factory=list,
        max_length=20,
        description="Отдельные ссылки на утверждения и их версии.",
    )
    _context_evidence: Any = PrivateAttr(default=None)
    message_references: list[ChatMessageReferenceSchema] = Field(
        default_factory=list,
        max_length=20,
        description="Проверенные цитаты сообщений текущей переписки; не знания проекта.",
    )


def encode_answer_sources(answer: KnowledgeAnswerSchema) -> list[dict]:
    """Сохраняет metadata в существующем JSON, не сохраняя private ledger."""
    return [
        *(source.model_dump(mode="json") for source in answer.sources),
        *(
            {"reference_type": "external", **source.model_dump(mode="json")}
            for source in answer.external_sources
        ),
        *(
            {"reference_type": "assertion", **source.model_dump(mode="json")}
            for source in answer.assertion_references
        ),
        *(
            [
                {
                    "reference_type": "context_guard",
                    "guard": answer._context_evidence[0].saved.model_dump(mode="json"),
                }
            ]
            if answer._context_evidence and answer._context_evidence[0].has_materials
            else []
        ),
    ]


def decode_answer_sources(values: list[dict]) -> dict:
    """Читает канонические tagged metadata и прежние обычные internal entries."""
    result = {"sources": [], "external_sources": [], "assertion_references": []}
    schemas = {
        "external": ("external_sources", ExternalSourceReferenceSchema),
        "assertion": ("assertion_references", AssertionReferenceSchema),
        None: ("sources", KnowledgeSourceSchema),
    }
    for value in values:
        kind = value.get("reference_type")
        if kind == "context_guard":
            continue
        if kind not in schemas:
            raise ValueError("Неизвестный вид metadata источника ответа.")
        key, schema = schemas[kind]
        payload = {name: item for name, item in value.items() if name != "reference_type"}
        result[key].append(schema.model_validate(payload))
    return result


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
