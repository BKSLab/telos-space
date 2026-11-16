"""Grounded literal extraction: проверяемые заявления без вывода скрытой цели.

Метод фиксирует то, что написано в актуальном первичном источнике. Он не
доказывает поведение продукта и не подменяет пользовательское принятие решения.
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from src.knowledge.catalog import FIELD_LABELS, ProjectCatalog, ProjectSource, SourceType
from src.utils.document_refs import document_text_hash, encode_document_ref
from src.utils.project_context import PROJECT_CONTEXT_FIELD_NAMES
from src.utils.project_description import DESCRIPTION_BLOCKS

MAX_FRAGMENT_CHARS = 32_000
MAX_BATCH_CHARS = 128_000
MAX_QUOTE_CHARS = 1600
MAX_FRAGMENT_CLAIMS = 256
MAX_BATCH_CLAIMS = 512
MAX_BATCH_SOURCES = 64
MAP_DEADLINE_SECONDS = 300
MAP_METHOD = "grounded_literal_v1"
MAP_JOB_ENTITY_ID = "project-map"
DOCUMENT_KINDS = {SourceType.DOCUMENT, SourceType.ATTACHMENT, SourceType.PROJECT_FILE}
ELIGIBLE_KINDS = DOCUMENT_KINDS | {SourceType.PROJECT, SourceType.TASK, SourceType.TASK_REPORT}
HEADINGS = {title.casefold(): name for name, title in DESCRIPTION_BLOCKS}
HEADINGS.update(
    {
        "требования": "constraints",
        "ограничения": "constraints",
        "решения": "decisions",
        "возможности": "capabilities",
        "вопросы": "open_questions",
        "связи": "relations",
    }
)


@dataclass(frozen=True, slots=True)
class EvidenceDraft:
    source_id: str
    quote: str | None = None
    start: int | None = None
    end: int | None = None
    field_path: str | None = None
    field_value: str | int | bool | None = None


@dataclass(frozen=True, slots=True)
class ClaimDraft:
    source_id: str
    stable_key: str
    category: str
    text: str
    kind: str
    acceptance: str
    verification: str
    provenance: dict
    evidence: tuple[EvidenceDraft, ...]
    subject: str | None = None
    value_hash: str | None = None

    def identifier(self, project_id: int) -> UUID:
        return uuid5(
            NAMESPACE_URL, f"telos-project-claim:{project_id}:{self.source_id}:{self.stable_key}"
        )


@dataclass(slots=True)
class SourceProjection:
    source: ProjectSource
    text: str
    representation: str
    document_ref: str | None
    native_revision: str | None
    dependencies: tuple[str, ...] = ()
    supported: bool = True
    skip_reason: str | None = None
    field_spans: list[tuple[str, int, int]] = field(default_factory=list)
    task_done: bool = False
    report_current: bool = False
    report_reason: str | None = None
    relationships: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def content_hash(self) -> str:
        return document_text_hash(self.text)


@dataclass(slots=True)
class ParsedSource:
    source_id: str
    offset: int
    end: int
    total: int
    drafts: list[ClaimDraft]
    gaps: list[dict]
    complete: bool


def _provenance(source: ProjectSource, origin: str, explanation: str) -> dict:
    properties = source.properties
    return {
        "origin": origin,
        "author_user_id": properties.get("authored_by_user_id")
        or properties.get("uploaded_by_user_id"),
        "author_name": properties.get("author_name"),
        "algorithm": MAP_METHOD,
        "independently_verified": False,
        "explanation": explanation,
    }


def build_projections(catalog: ProjectCatalog) -> dict[str, SourceProjection]:
    """Проецирует только существующий разрешённый каталог и известную семантику.

    Args:
        catalog: Согласованный PostgreSQL-снимок одного проекта.
    Returns:
        Тексты/metadata и явные ограничения, без копии оригиналов в новом store.
    """
    task_rows = {row["id"]: row for row in catalog.rows.get("tasks", [])}
    stages = {row["id"]: row for row in catalog.rows.get("project_stages", [])}
    reports = {}
    for row in catalog.rows.get("task_reports", []):
        previous = reports.get(row["task_id"])
        if previous is None or row["revision"] > previous["revision"]:
            reports[row["task_id"]] = row
    done_names = {row["name"] for row in stages.values() if row.get("is_done_stage")}
    active_transition = {}
    for row in catalog.rows.get("task_activity", []):
        if row.get("event_type") == "STAGE_CHANGED" and row.get("to_value") not in done_names:
            observed = _date(row.get("created_at"))
            if observed and (
                row["task_id"] not in active_transition
                or observed > active_transition[row["task_id"]]
            ):
                active_transition[row["task_id"]] = observed
    result = {}
    for source in catalog.sources.values():
        document = source.kind in DOCUMENT_KINDS
        raw = source.document_text if document and source.document_text is not None else source.text
        revision = (
            source.properties.get("context_revision")
            if source.kind is SourceType.PROJECT
            else source.properties.get("version", source.properties.get("revision"))
        )
        dependencies = []
        if source.parent_source_id and source.parent_source_id.startswith("task:"):
            dependencies.append(source.parent_source_id)
        projection = SourceProjection(
            source=source,
            text=raw,
            representation=source.document_rendition or "catalog_text_v2",
            document_ref=encode_document_ref(source.kind.value, source.entity_id)
            if document
            else None,
            native_revision=str(revision) if revision is not None else None,
            supported=source.kind in ELIGIBLE_KINDS,
        )
        if not projection.supported:
            projection.skip_reason = "metadata_only_source"
        if document and source.document_text is None:
            projection.supported = False
            projection.skip_reason = "text_not_ready"
        if source.kind in DOCUMENT_KINDS | {SourceType.TASK}:
            for link in source.relations:
                if link["relation"] in {
                    "document_of",
                    "depends_on",
                    "precedes",
                    "in_wbs",
                    "derived_from",
                }:
                    target = catalog.sources[link["source_id"]]
                    projection.relationships.append(
                        (link["relation"], target.source_id, target.title)
                    )
        if source.kind is SourceType.PROJECT:
            sections = source.properties.get("description_sections")
            values = (
                sections
                if sections is not None
                else {"additional": source.properties.get("description_md") or ""}
            )
            description = source.properties.get("description_md") or ""
            heading = f"## {FIELD_LABELS['description_md']}\n"
            description_start = source.text.find(heading + description) if description else -1
            cursor = (
                description_start + len(heading) if description_start >= 0 else len(source.text)
            )
            for name, _ in DESCRIPTION_BLOCKS:
                value = values.get(name, "")
                if not isinstance(value, str) or not value.strip():
                    continue
                start = source.text.find(value, cursor)
                if start >= 0:
                    projection.field_spans.append((name, start, start + len(value)))
                    cursor = start + len(value)
        if source.kind in {SourceType.TASK, SourceType.TASK_REPORT}:
            task_id = (
                source.entity_id if source.kind is SourceType.TASK else source.properties["task_id"]
            )
            task = task_rows.get(task_id, {})
            stage = stages.get(task.get("stage_id"), {})
            projection.task_done = bool(stage.get("is_done_stage")) or task.get("status") == "DONE"
            if stage:
                dependencies.append(f"stage:{stage['id']}")
            if source.kind is SourceType.TASK_REPORT:
                dependencies.extend([f"task:{task_id}", f"project:{catalog.project_id}"])
                task_source = catalog.sources.get(f"task:{task_id}")
                if task_source:
                    dependencies.extend(
                        link["source_id"]
                        for link in task_source.relations
                        if link["relation"] in {"contains", "has_document"}
                        and link["source_id"].split(":", 1)[0]
                        in {"document", "attachment", "project_file"}
                    )
                latest = reports.get(task_id)
                report_date = _date(source.properties.get("created_at"))
                changed = active_transition.get(task_id)
                projection.report_current = bool(
                    projection.task_done
                    and latest
                    and latest["id"] == source.entity_id
                    and (changed is None or report_date is not None and report_date >= changed)
                )
                if not projection.report_current:
                    projection.supported = False
                    projection.skip_reason = "task_report_not_current"
                body = source.properties.get("body_md") or ""
                heading = f"## {FIELD_LABELS['body_md']}\n"
                marker = raw.find(heading + body) if body else -1
                body_start = marker + len(heading) if marker >= 0 else -1
                if body_start >= 0:
                    projection.field_spans = [("task_results", body_start, body_start + len(body))]
                else:
                    projection.supported = False
                    projection.skip_reason = "task_report_empty"
            else:
                latest = reports.get(task_id)
                report_date = _date(latest.get("created_at")) if latest else None
                changed = active_transition.get(task_id)
                projection.report_current = bool(
                    projection.task_done
                    and latest
                    and (changed is None or report_date is not None and report_date >= changed)
                )
        dependencies.extend(target for _, target, _ in projection.relationships)
        projection.dependencies = tuple(
            sorted(
                set(
                    value
                    for value in dependencies
                    if value in catalog.sources and value != source.source_id
                )
            )
        )
        result[source.source_id] = projection
    return result


def parse_projection(
    projection: SourceProjection,
    *,
    offset: int,
    max_chars: int,
    max_claims: int = MAX_FRAGMENT_CLAIMS,
) -> ParsedSource:
    """Извлекает ограниченный Unicode-фрагмент с точными координатами.

    Args:
        projection: Проверенный первичный источник и его зависимости.
        offset: Сохранённый checkpoint этой версии.
        max_chars: Оставшийся бюджет batch, до 32 000 символов за фрагмент.
        max_claims: Ограничение сохраняемых утверждений фрагмента.
    Returns:
        Точные statements, пробелы и следующий checkpoint; никакой записи паспорта.
    """
    source = projection.source
    if not projection.supported:
        gaps = []
        if projection.skip_reason in {
            "text_not_ready",
            "task_report_not_current",
            "task_report_empty",
        }:
            gaps.append(
                {
                    "code": projection.skip_reason,
                    "category": "task_results"
                    if source.kind is SourceType.TASK_REPORT
                    else "processing",
                    "source_id": source.source_id,
                    "message": "Текущий текст ещё не подготовлен."
                    if projection.skip_reason == "text_not_ready"
                    else "Отчёт не используется как текущий результат: задача открыта, есть новая публикация или изменена стадия после отчёта.",
                }
            )
        return ParsedSource(source.source_id, 0, 0, 0, [], gaps, True)
    if source.kind is SourceType.TASK:
        result = _task_status(projection)
        result.drafts.extend(_relationships(projection))
        if len(projection.relationships) > 16:
            result.gaps.append(_relationship_gap(source.source_id))
        return result
    end = min(len(projection.text), offset + min(max_chars, MAX_FRAGMENT_CHARS))
    drafts = _relationships(projection) if offset == 0 else []
    gaps = []
    if offset == 0 and len(projection.relationships) > 16:
        gaps.append(_relationship_gap(source.source_id))
    if source.kind is SourceType.PROJECT:
        structured = source.properties.get("description_sections") is not None
        for name, begin, finish in projection.field_spans:
            start = max(begin, offset)
            stop = min(finish, end)
            if stop <= start:
                continue
            for location, quote in _pieces(projection.text, start, stop):
                requirement = structured and name in {
                    "problem",
                    "goal",
                    "expected_result",
                    "audience",
                    "boundaries",
                    "constraints",
                }
                kind = (
                    "open_question"
                    if name == "open_questions"
                    else "requirement"
                    if requirement
                    else "source_fact"
                )
                # Разные строки одного многострочного поля не являются
                # противоположными значениями одного предмета.
                subject, value_hash = (
                    _subject(quote)
                    if quote == projection.text[begin:finish].strip()
                    else (None, None)
                )
                drafts.append(
                    ClaimDraft(
                        source.source_id,
                        f"passport:{name}:{location - begin}",
                        name,
                        quote,
                        kind,
                        "accepted" if structured else "unreviewed",
                        "owner_statement" if structured else "source_statement",
                        _provenance(
                            source,
                            "project_passport",
                            "Авторское поле паспорта. Это постановка/заявление человека, а не независимая проверка поведения."
                            if structured
                            else "Прежний свободный текст сохранён как дополнительное заявление; цель из его заголовков не выведена.",
                        ),
                        (EvidenceDraft(source.source_id, quote, location, location + len(quote)),),
                        subject=subject,
                        value_hash=value_hash,
                    )
                )
                if len(drafts) >= max_claims:
                    end = location + len(quote)
                    break
            if len(drafts) >= max_claims:
                break
        if offset == 0:
            values = source.properties.get("description_sections") or {}
            projected = {name for name, _, _ in projection.field_spans}
            for name in PROJECT_CONTEXT_FIELD_NAMES:
                if values.get(name) and name not in projected:
                    gaps.append(
                        {
                            "code": "passport_projection_missing",
                            "category": name,
                            "source_id": source.source_id,
                            "message": "Сохранённое поле не найдено в текущем текстовом представлении; его значение не подменено совпадением в заголовке.",
                        }
                    )
                if not values.get(name):
                    if (
                        not structured
                        and name == "additional"
                        and source.properties.get("description_md")
                    ):
                        continue
                    gaps.append(
                        {
                            "code": "passport_field_unknown",
                            "category": name,
                            "source_id": source.source_id,
                            "message": f"Поле паспорта «{name}» не заполнено; значение не выведено из документов или старых задач.",
                        }
                    )
    else:
        if source.kind is SourceType.TASK_REPORT and offset == 0:
            gaps.append(
                {
                    "code": "task_result_verification_missing",
                    "category": "task_results",
                    "source_id": source.source_id,
                    "message": "Сохранено заявление участника с текущим контекстом паспорта, задачи, checklist и документов. Выполнение критериев, CI и работу в prod этот метод независимо не проверяет.",
                }
            )
        content_start = (
            projection.field_spans[0][1]
            if source.kind is SourceType.TASK_REPORT and projection.field_spans
            else 0
        )
        content_end = (
            projection.field_spans[0][2]
            if source.kind is SourceType.TASK_REPORT and projection.field_spans
            else len(projection.text)
        )
        for location, quote in _pieces(
            projection.text, max(offset, content_start), min(end, content_end)
        ):
            if not quote.strip() or re.fullmatch(r"[\s#*_\-|]+", quote):
                continue
            category = (
                "task_results"
                if source.kind is SourceType.TASK_REPORT
                else _category(projection.text, location)
            )
            report = source.kind is SourceType.TASK_REPORT
            prefix = "Участник сообщил: " if report else "В источнике указано: "
            evidence = [EvidenceDraft(source.source_id, quote, location, location + len(quote))]
            # Полный dependency manifest проверяется у source version. В DTO
            # достаточно bounded структурированных контекстных доказательств.
            evidence.extend(
                EvidenceDraft(value, field_path="source_generation")
                for value in projection.dependencies[:4]
            )
            subject, value_hash = _subject(quote)
            drafts.append(
                ClaimDraft(
                    source.source_id,
                    f"text:{location}",
                    category,
                    prefix + quote,
                    "source_fact",
                    "unreviewed",
                    "participant_report" if report else "source_statement",
                    _provenance(
                        source,
                        "task_report" if report else "project_document",
                        "Опубликованное сообщение участника о результате. Оно не доказывает прохождение критериев, CI или выкладку в prod."
                        if report
                        else "Зафиксировано дословное заявление текущей версии документа. Оно не становится утверждённым требованием человека.",
                    ),
                    tuple(evidence),
                    subject=subject,
                    value_hash=value_hash,
                )
            )
            if len(drafts) >= max_claims:
                end = location + len(quote)
                break
    if end < len(projection.text):
        gaps.append(
            {
                "code": "source_partial",
                "category": "processing",
                "source_id": source.source_id,
                "message": "Источник продолжается с checkpoint; непроанализированный хвост не объявлен пустым.",
            }
        )
    return ParsedSource(
        source.source_id,
        offset,
        end,
        len(projection.text),
        drafts,
        gaps,
        end >= len(projection.text),
    )


def _task_status(projection: SourceProjection) -> ParsedSource:
    source = projection.source
    if not projection.task_done or projection.report_current:
        return ParsedSource(source.source_id, 0, 0, 0, [], [], True)
    stage = next((value for value in projection.dependencies if value.startswith("stage:")), None)
    evidence = [
        EvidenceDraft(
            source.source_id,
            field_path="properties.stage_id",
            field_value=source.properties.get("stage_id"),
        )
    ]
    if stage:
        evidence.append(
            EvidenceDraft(stage, field_path="properties.is_done_stage", field_value=True)
        )
    draft = ClaimDraft(
        source.source_id,
        "task:done-without-report",
        "task_results",
        "Задача переведена в завершающее состояние; опубликованный результат отсутствует.",
        "source_fact",
        "unreviewed",
        "status_only",
        _provenance(
            source,
            "task_state",
            "Подтверждена только смена состояния. Исполнение требований и продакшен не подтверждены.",
        ),
        tuple(evidence),
    )
    gap = {
        "code": "task_report_missing",
        "category": "task_results",
        "source_id": source.source_id,
        "message": "Закрытие задачи без опубликованного результата не подтверждает выполнение или выкладку.",
    }
    return ParsedSource(source.source_id, 0, 0, 0, [draft], [gap], True)


def _relationships(projection: SourceProjection) -> list[ClaimDraft]:
    labels = {
        "document_of": "связан с задачей",
        "depends_on": "зависит от",
        "precedes": "предшествует",
        "in_wbs": "размещён в разделе",
        "derived_from": "получен из",
    }
    return [
        ClaimDraft(
            projection.source.source_id,
            f"relation:{relation}:{target}",
            "relations",
            f"«{projection.source.title[:500]}» {labels[relation]} «{title[:500]}».",
            "source_fact",
            "unreviewed",
            "source_statement",
            _provenance(
                projection.source,
                "project_record",
                "Явная связь текущего каталога. Она не означает выполнение задачи или принятие требования.",
            ),
            (
                EvidenceDraft(
                    projection.source.source_id,
                    field_path=f"relations.{relation}",
                    field_value=target,
                ),
                EvidenceDraft(target, field_path="source_generation"),
            ),
        )
        for relation, target, title in projection.relationships[:16]
    ]


def _relationship_gap(source_id: str) -> dict:
    return {
        "code": "relation_limit",
        "category": "relations",
        "source_id": source_id,
        "message": "Показаны первые 16 явных связей источника; полный граф остаётся в каталоге и не объявлен исчерпывающим.",
    }


def _pieces(text_value: str, begin: int, end: int):
    cursor = begin
    while cursor < end:
        stop = min(end, cursor + MAX_QUOTE_CHARS)
        split = text_value.find("\n", cursor, stop)
        if split >= cursor:
            stop = split + 1
        raw = text_value[cursor:stop]
        left = len(raw) - len(raw.lstrip())
        quote = raw.strip()
        if quote:
            yield cursor + left, quote
        cursor = stop


def _category(text_value: str, location: int) -> str:
    # Ограниченная ближайшая явная Markdown-метка, без интерпретации скрытой цели.
    prefix = text_value[max(0, location - MAX_FRAGMENT_CHARS) : location]
    headings = re.findall(r"(?m)^#{1,6}\s+([^\n]+)", prefix)
    return HEADINGS.get(headings[-1].strip().casefold(), "facts") if headings else "facts"


def _subject(quote: str) -> tuple[str | None, str | None]:
    match = re.fullmatch(r"([\w \-./()]{2,100}):\s*([^\n]{1,1000})", quote.strip())
    if not match:
        return None, None
    return " ".join(match[1].casefold().split()), _hash(match[2].strip().casefold())


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _date(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
