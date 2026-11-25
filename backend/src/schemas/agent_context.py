"""Аргументы инструментов контекста; actor, ACL и proofs модель не задаёт."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.schemas.web_context import MAX_WEB_QUERY_CHARS, WebSearchRequest


class ContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContextPageInput(ContextInput):
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0)


class GitSearchInput(ContextInput):
    query: str = Field(min_length=1, max_length=1000)
    connection_ids: list[int] = Field(default_factory=list, max_length=10)
    limit: int = Field(default=5, ge=1, le=10)
    sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")

    @model_validator(mode="after")
    def pinned_repository(self):
        """SHA всегда относится к одному явно выбранному подключению."""
        if self.sha is not None and len(self.connection_ids) != 1:
            raise ValueError("Для SHA выберите ровно одно подключение.")
        return self


class GitSnapshotsInput(ContextPageInput):
    connection_id: int = Field(gt=0)


class GitSymbolsInput(GitSnapshotsInput):
    sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    path: str | None = Field(default=None, min_length=1, max_length=4096)
    query: str | None = Field(default=None, min_length=1, max_length=512)
    symbol_id: int | None = Field(default=None, gt=0)


class GitGraphInput(ContextInput):
    connection_id: int = Field(gt=0)
    sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    root_id: int | None = Field(default=None, gt=0)
    direction: Literal["in", "out", "both"] = "both"
    depth: int = Field(default=1, ge=1, le=3)
    limit: int = Field(default=50, ge=1, le=100)
    relation: Literal["all", "dependency", "containment"] = "all"


class GitImpactInput(ContextInput):
    connection_id: int = Field(gt=0)
    sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    symbol_id: int = Field(gt=0)
    depth: int = Field(default=1, ge=1, le=3)
    limit: int = Field(default=50, ge=1, le=100)


class GitReadInput(ContextInput):
    connection_id: int = Field(gt=0)
    sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    path: str = Field(min_length=1, max_length=1024)
    offset: int = Field(default=0, ge=0)
    max_chars: int = Field(default=8000, ge=1, le=8000)


class TaskGitLinksInput(ContextInput):
    task_id: int = Field(gt=0)


class WebTopicInput(ContextInput):
    public_topic: str = Field(min_length=1, max_length=MAX_WEB_QUERY_CHARS)

    @field_validator("public_topic")
    @classmethod
    def canonical_public_topic(cls, value: str) -> str:
        """Validate before pending; consent and provider receive the same topic."""
        return WebSearchRequest(query=value).query


class WebSaveInput(ContextInput):
    page_token: str = Field(min_length=1, max_length=8192)
    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str = Field(min_length=1, max_length=200)
