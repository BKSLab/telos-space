"""Серверные квитанции чтения: dict сериализуется, private proofs — нет."""

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from src.schemas.knowledge import AssertionReferenceSchema, ExternalSourceReferenceSchema


def git_change_reference(project_id, item) -> ExternalSourceReferenceSchema:
    """Связь с изменением остаётся отдельной от файлов и от утверждений карты."""
    return ExternalSourceReferenceSchema(
        source_id=f"git_change:{item.change_id}:{item.task_id}:{item.id}",
        kind="git_change",
        project_id=project_id,
        title=item.title[:512],
        url=item.source_url,
        task_id=item.task_id,
        change_id=item.change_id,
        connection_id=item.connection_id,
        repository_id=item.repository_id,
        commit_sha=item.merge_sha or item.commit_sha,
        excerpt=json.dumps(
            {
                "status": item.status,
                "evidence": [value.model_dump(mode="json") for value in item.evidence[:6]],
            },
            ensure_ascii=False,
        )[:1200],
        fetched_at=item.checked_at,
        coverage="partial_snapshot" if item.evidence_partial else "snapshot",
    )


@dataclass(frozen=True, slots=True)
class WebPageObservation:
    page_token: str
    source_id: str
    url: str
    content_hash: str
    coverage: str


class ContextReadResult(dict[str, Any]):
    """Единственный trusted marker результата общего context-service.

    Произвольный JSON документа/вложения не создаёт такой объект и не получает
    project scope или право регистрировать внешний источник.
    """

    def __init__(
        self,
        value: dict,
        *,
        project_id: int,
        observations: tuple[BaseModel, ...] = (),
        project_reads: tuple[BaseModel, ...] = (),
        restricted: bool = True,
        origin_read_scopes: frozenset[str] = frozenset(),
        git_proofs: tuple[BaseModel, ...] = (),
        web_pages: tuple[WebPageObservation, ...] = (),
        external_sources: tuple[ExternalSourceReferenceSchema, ...] = (),
        assertions: tuple[AssertionReferenceSchema, ...] = (),
    ) -> None:
        super().__init__(value)
        self.project_id = project_id
        self.observations = observations
        self.project_reads, self.restricted = project_reads, restricted
        self.origin_read_scopes = origin_read_scopes
        self.git_proofs = git_proofs
        self.web_pages = web_pages
        self.external_sources = external_sources
        self.assertions = assertions


class HistoryReadResult(dict[str, Any]):
    """Транзитивные guards истории не входят в JSON, который читает модель."""

    def __init__(self, value: dict, *, guards: tuple[BaseModel, ...]) -> None:
        super().__init__(value)
        self.guards = guards
