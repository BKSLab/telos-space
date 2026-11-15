"""Обнаружение документов инструментами: Task DTO не является перечнем документов."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.schemas.project_tool_inputs import DocumentListInput
from src.schemas.tasks import TaskSchema
from src.services.agent_personal_actions import _task_payload


@pytest.mark.parametrize("scope", ["all", "tasks"])
def test_task_document_listing_accepts_task_scopes(scope):
    assert DocumentListInput(task_id=7, scope=scope).task_id == 7


def test_task_document_listing_rejects_project_only_filter_with_task():
    with pytest.raises(ValidationError, match="scope=all"):
        DocumentListInput(task_id=7, scope="project")
    assert DocumentListInput(scope="project").task_id is None


def test_personal_task_result_marks_documents_unread_instead_of_empty():
    task_schema = TaskSchema(
        id=7, kind="PERSONAL", status="NEW", project_id=None, stage_id=None, number=1, key="ME-1", title="Личная задача",
        priority="MEDIUM", position=1, created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    result = _task_payload(task_schema)
    assert result["documents"] == {
        "loaded": False,
        "tool": "telos_list_documents",
        "arguments": {"task_id": task_schema.id, "scope": "all"},
    }
    assert "items" not in result["documents"]
