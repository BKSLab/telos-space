"""Аргументы инструментов контекста; actor, ACL и proofs модель не задаёт."""

from pydantic import BaseModel, ConfigDict, Field


class ContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContextPageInput(ContextInput):
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0)


class GitSearchInput(ContextInput):
    query: str = Field(min_length=1, max_length=1000)
    connection_ids: list[int] = Field(default_factory=list, max_length=10)
    limit: int = Field(default=5, ge=1, le=10)


class GitReadInput(ContextInput):
    connection_id: int = Field(gt=0)
    sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    path: str = Field(min_length=1, max_length=1024)
    offset: int = Field(default=0, ge=0)
    max_chars: int = Field(default=8000, ge=1, le=8000)


class TaskGitLinksInput(ContextInput):
    task_id: int = Field(gt=0)


class WebTopicInput(ContextInput):
    public_topic: str = Field(min_length=1, max_length=600)


class WebSaveInput(ContextInput):
    page_token: str = Field(min_length=1, max_length=8192)
    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str = Field(min_length=1, max_length=200)
