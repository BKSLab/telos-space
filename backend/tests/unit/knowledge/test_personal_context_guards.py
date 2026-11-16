"""Ответ без citations также не может публиковать старый personal-task payload."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.agent.tools import AgentToolRequest
from src.exceptions.ai import AgentAnswerError
from src.exceptions.context_selection import ContextSelectionChangedError
from src.services.agent_action_helpers import task_model_payload
from src.services.agent_personal_actions import _compact
from src.services.project_agent import ProjectAgentService, _SourceRegistry
from tests.unit.knowledge.agent_models import LegacyOutput
from tests.unit.knowledge.test_task_answer_sources import ask_personal, personal_service
from tests.unit.services.test_context_evidence import evidence_environment


async def test_personal_task_changed_after_model_read_is_refused_without_model_source_handles():
    service, clients, _, db, initial, current = personal_service()
    db.tasks.get_task.side_effect = [initial, current, current]

    async def respond(*, content, **_kwargs):
        if not json.loads(content).get("read_results"):
            return LegacyOutput(
                tool_calls=[
                    AgentToolRequest(name="telos_get_any_task", arguments={"task_id": initial.id})
                ]
            )
        return LegacyOutput(answer="Использован исходный текст задачи.", source_ids=[])

    clients.llm_client.get_structured_response.side_effect = respond
    with pytest.raises(AgentAnswerError, match="версия задачи изменились"):
        await ask_personal(service)


async def test_summary_task_guard_uses_read_projection_and_still_rejects_changed_task():
    _, _, _, _, original, _ = personal_service()
    _, verifier, principal, *_ = evidence_environment()
    async with verifier.task_scope() as db:
        db.access.ensure_task_access = AsyncMock()
        db.tasks = SimpleNamespace(get_by_id=AsyncMock(return_value=original))
    compact = _compact(original.model_dump(mode="json"))
    registry = _SourceRegistry()
    ProjectAgentService._register_personal_views({"items": [compact]}, registry)
    assert registry.evidence.saved.tasks[0].projection == "summary"
    await verifier.validate(registry.evidence.saved, principal=principal)
    db.tasks.get_by_id.return_value = original.model_copy(update={"title": "Изменённое название"})
    with pytest.raises(ContextSelectionChangedError):
        await verifier.validate(registry.evidence.saved, principal=principal)


def test_model_task_payload_has_canonical_read_pointers_without_embedded_other_source_text():
    _, _, _, _, original, _ = personal_service(project_id=1)
    payload = task_model_payload(original, project_bound=True)
    assert not {"report", "last_comment", "comments_count"} & payload["task"].keys()
    assert payload["source_reads"]["report"] == {
        "loaded": False,
        "read": {"tool": "telos_get_task_report", "arguments": {"task_id": original.id}},
    }
    assert payload["source_reads"]["comments"]["loaded"] is False
    assert payload["source_reads"]["comments"]["read"]["tool"] == "telos_list_comments"
