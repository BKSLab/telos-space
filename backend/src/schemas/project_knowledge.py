"""Проверяемая производная карта: тексты источников не заменяют паспорт."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

KnowledgeCategory = Literal[
    "problem",
    "goal",
    "expected_result",
    "additional",
    "audience",
    "capabilities",
    "boundaries",
    "constraints",
    "glossary",
    "decisions",
    "open_questions",
    "facts",
    "relations",
    "task_results",
]
KnowledgeClaimKind = Literal[
    "requirement", "source_fact", "inference", "assumption", "open_question"
]
KnowledgeClaimState = Literal["current", "stale", "superseded", "conflicting", "unavailable"]
KnowledgeMapState = Literal["empty", "pending", "updating", "ready", "partial", "failed"]


class ProjectKnowledgeSourceObservationSchema(BaseModel):
    """Серверный отпечаток использованного источника без его содержимого."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(max_length=256, description="Канонический ID источника в проекте.")
    generation: int = Field(ge=0, description="Монотонное поколение изменений источника.")
    source_version_id: UUID | None = Field(
        default=None, description="Сохранённая версия, если источник уже обработан."
    )
    source_hash: str | None = Field(
        default=None, description="Отпечаток сохранённого снимка источника."
    )
    content_hash: str | None = Field(
        default=None, description="SHA-256 представления прочитанного текста."
    )


class ProjectKnowledgeObservationSchema(BaseModel):
    """Набор для повторной проверки перед публикацией, включая A→B→A."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: int = Field(gt=0, description="Проект использованного контекста.")
    generation: int = Field(ge=0, description="Поколение проекта в момент чтения.")
    sources: list[ProjectKnowledgeSourceObservationSchema] = Field(
        default_factory=list,
        max_length=256,
        description="Все прочитанные источники; handles модели не определяют этот список.",
    )


class ProjectKnowledgeEvidenceSchema(BaseModel):
    """Точная цитата или проверенное структурированное поле той же версии."""

    source_version_id: UUID = Field(description="ID неизменяемой версии источника.")
    source_id: str = Field(description="Канонический ID источника.")
    source_generation: int = Field(ge=0, description="Поколение источника.")
    native_revision: str | None = Field(
        default=None, description="Собственная ревизия документа, паспорта или отчёта."
    )
    source_hash: str = Field(description="Хэш снимка источника.")
    content_hash: str = Field(description="Хэш полного представления текста.")
    representation: str = Field(description="Представление, в котором заданы координаты.")
    document_ref: str | None = Field(default=None, description="Ссылка общего каталога документов.")
    task_id: int | None = Field(default=None, description="Текущая задача источника.")
    title: str = Field(description="Название проверенной версии источника.")
    quote: str | None = Field(
        default=None,
        max_length=2000,
        description="Дословная цитата; null для структурированного поля.",
    )
    start: int | None = Field(default=None, ge=0, description="Начало цитаты в Unicode-символах.")
    end: int | None = Field(default=None, ge=0, description="Конец цитаты, не включительно.")
    offset_unit: Literal["unicode_codepoints"] = Field(
        default="unicode_codepoints", description="Единица координат цитаты."
    )
    field_path: str | None = Field(
        default=None, description="Путь структурированного факта без выдуманной цитаты."
    )
    value: str | int | bool | None = Field(
        default=None, description="Проверенное значение структурированного поля."
    )
    checked_at: datetime = Field(description="Когда проверены версия и фрагмент.")
    coverage: Literal["exact_fragment", "structured_field"] = Field(
        description="Что именно проверено этим доказательством."
    )


class ProjectKnowledgeProvenanceSchema(BaseModel):
    """Происхождение утверждения, отдельно от принятия и проверки поведения."""

    origin: Literal[
        "project_passport",
        "project_document",
        "task_report",
        "task_state",
        "project_record",
        "derived",
    ] = Field(description="Первичный контур утверждения.")
    author_user_id: int | None = Field(
        default=None, description="Достоверный ID автора, если он сохранён в источнике."
    )
    author_name: str | None = Field(
        default=None, description="Сохранённое имя автора, без угадывания по тексту."
    )
    algorithm: str = Field(
        default="grounded_literal_v1",
        description="Версия извлечения, не модель доказательства истины.",
    )
    independently_verified: bool = Field(
        default=False, description="Подтверждено ли поведение независимо от заявления участника."
    )
    explanation: str = Field(description="Что источник подтверждает и чего из него не следует.")


class ProjectKnowledgeClaimSchema(BaseModel):
    """Утверждение с текущей проверкой всех evidence и явным происхождением."""

    id: UUID = Field(description="Стабильный ID утверждения.")
    revision: int = Field(ge=1, description="Версия утверждения и набора доказательств.")
    kind: KnowledgeClaimKind = Field(
        description="Смысл утверждения; свежий вывод не становится требованием человека."
    )
    category: KnowledgeCategory = Field(description="Раздел производной карты.")
    text: str | None = Field(
        max_length=2000,
        description="Текст; null после изменения/удаления/утраты доступа к доказательству.",
    )
    state: KnowledgeClaimState = Field(description="Актуальность этой версии доказательств.")
    acceptance: Literal["accepted", "unreviewed", "rejected"] = Field(
        description="Принятие человеком, независимо от свежести."
    )
    verification: Literal[
        "owner_statement",
        "source_statement",
        "participant_report",
        "status_only",
        "analysis",
        "unknown",
    ] = Field(description="Сила подтверждения; закрытие задачи не подтверждает продакшен.")
    provenance: ProjectKnowledgeProvenanceSchema = Field(
        description="Происхождение без смешения первичного и производного."
    )
    source_ids: list[str] = Field(
        default_factory=list, description="Текущие источники утверждения."
    )
    evidence: list[ProjectKnowledgeEvidenceSchema] = Field(
        default_factory=list, description="Проверенные текущие версии и фрагменты."
    )
    conflicts_with: list[UUID] = Field(
        default_factory=list,
        max_length=20,
        description="До 20 текущих утверждений с явно разошедшимся значением предметного ключа; список не исчерпывающий.",
    )
    updated_at: datetime = Field(description="Время фиксации версии утверждения.")
    replaced_by: int | None = Field(
        default=None, description="Новая ревизия того же утверждения, если оно заменено."
    )
    reason: str | None = Field(
        default=None, description="Причина устаревания, неполноты или конфликта."
    )


class ProjectKnowledgeGapSchema(BaseModel):
    """Пробел в знаниях, а не выдуманный отрицательный факт о продукте."""

    code: str = Field(description="Каноническая причина пробела.")
    category: str = Field(description="Раздел знаний или обработки.")
    source_id: str | None = Field(
        default=None, description="Разрешённый текущий источник, если причина локальна."
    )
    message: str = Field(description="Понятное объяснение отсутствия/ограничения данных.")


class ProjectKnowledgeCoverageSchema(BaseModel):
    """Охват объявленного метода, не гарантия полноты смысла проекта."""

    sources_total: int = Field(
        default=0, ge=0, description="Источников в текущем объявленном охвате."
    )
    sources_current: int = Field(default=0, ge=0, description="Обработано текущих источников.")
    sources_pending: int = Field(
        default=0, ge=0, description="Ожидают текущей версии или продолжения."
    )
    sources_failed: int = Field(
        default=0, ge=0, description="Источники с видимой ошибкой обработки."
    )
    sources_skipped: int = Field(
        default=0, ge=0, description="Источники вне объявленного текстового метода."
    )
    chars_total: int = Field(default=0, ge=0, description="Размер объявленного текстового охвата.")
    chars_processed: int = Field(default=0, ge=0, description="Обработанный объём текущих версий.")
    claims_current: int = Field(
        default=0, ge=0, description="Текущие непротиворечивые утверждения."
    )
    claims_conflicting: int = Field(
        default=0, ge=0, description="Утверждения с явным расхождением."
    )
    claims_stale: int = Field(
        default=0, ge=0, description="Версии, скрытые из-за изменения доказательств."
    )
    complete: bool = Field(
        default=False,
        description="Завершён ли объявленный охват обработки, не семантическая проверка истины.",
    )
    reasons: list[str] = Field(default_factory=list, description="Причины ограниченного охвата.")
    method: Literal["grounded_literal_v1"] = Field(
        default="grounded_literal_v1",
        description="Только точные заявления и структурированные факты; семантического доказательства нет.",
    )


class ProjectKnowledgeMetricsSchema(BaseModel):
    """Фактические показатели map-stage отдельно от существующего semantic index."""

    source_reads: int = Field(
        default=0, ge=0, description="Прочитано текстовых фрагментов в этой попытке."
    )
    sources_reused: int = Field(
        default=0, ge=0, description="Переиспользовано неизменных результатов."
    )
    chars_processed: int = Field(default=0, ge=0, description="Обработано символов в этой попытке.")
    catalog_chars_read: int = Field(
        default=0,
        ge=0,
        description="Фактически загруженный объём существующего каталогового снимка.",
    )
    duration_ms: float = Field(default=0, ge=0, description="Измеренная длительность попытки.")
    model_requests: int = Field(
        default=0, ge=0, description="Фактические модельные запросы map-stage."
    )
    http_attempts: int = Field(
        default=0, ge=0, description="Внешние HTTP-попытки map-stage, включая повторы."
    )
    input_tokens: int = Field(
        default=0, ge=0, description="Фактически учтённые входные токены map-stage."
    )
    output_tokens: int = Field(
        default=0, ge=0, description="Фактически учтённые выходные токены map-stage."
    )
    cost_usd: float | None = Field(
        default=None,
        ge=0,
        description="Фактическая стоимость при наличии; null не означает нулевую цену модели.",
    )
    cost_note: str = Field(
        default="no_model_requests", description="Почему цена не применима или неизвестна."
    )


class ProjectKnowledgeStatusSchema(BaseModel):
    """Свежесть карты и видимое состояние фоновой очереди."""

    project_id: int = Field(gt=0, description="Проект карты.")
    generation: int = Field(ge=0, description="Последнее поколение исходных изменений.")
    built_generation: int | None = Field(
        default=None, ge=0, description="Последнее опубликованное поколение."
    )
    status: KnowledgeMapState = Field(description="Состояние обработки текущего охвата.")
    updated_at: datetime | None = Field(
        default=None, description="Когда зафиксировано последнее обновление."
    )
    coverage: ProjectKnowledgeCoverageSchema = Field(
        default_factory=ProjectKnowledgeCoverageSchema, description="Текущий проверенный охват."
    )
    last_error: str | None = Field(default=None, description="Безопасная причина последнего сбоя.")
    metrics: ProjectKnowledgeMetricsSchema = Field(
        default_factory=ProjectKnowledgeMetricsSchema,
        description="Измерения последней map-попытки.",
    )


class ProjectKnowledgeMapSchema(ProjectKnowledgeStatusSchema):
    """Страница текущей карты без скрытого вызова модели и старого текста."""

    items: list[ProjectKnowledgeClaimSchema] = Field(
        default_factory=list, description="Доступные текущие/конфликтующие утверждения."
    )
    gaps: list[ProjectKnowledgeGapSchema] = Field(
        default_factory=list,
        max_length=32,
        description="До 31 пробела и явная отметка числа пропущенных, обработка и границы метода.",
    )
    observation: ProjectKnowledgeObservationSchema = Field(
        description="Серверный набор для повторной проверки перед ответом."
    )
    total: int = Field(ge=0, description="Всего доступных утверждений по фильтру.")
    offset: int = Field(ge=0, description="Пропущено утверждений.")
    limit: int = Field(ge=1, le=50, description="Размер страницы, до 50.")
    has_more: bool = Field(description="Есть ли следующая страница.")
    next_offset: int | None = Field(default=None, description="Смещение следующей страницы.")
