import json
from contextlib import asynccontextmanager
from dataclasses import fields
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.exceptions.access import ResourceNotAvailableError
from src.exceptions.ai import AiProviderError
from src.exceptions.clients import LlmClientError
from src.schemas.risk_suggestions import RiskSuggestionDraftSetSchema
from src.services.risk_suggestions import RiskSuggestionScope, RiskSuggestionService


def draft(**changes):
    return dict(
        title="Задержка CRM",
        description="Срок поставки может сдвинуться.",
        probability="HIGH",
        impact="MEDIUM",
        response_strategy="MITIGATE",
        mitigation_plan="Согласовать контракт",
        response_plan="Резервный адаптер",
        task_key="PROJ-12",
        evidence_refs=["S2"],
        **changes,
    )


def setup_service():
    db = RiskSuggestionScope(**{field.name: AsyncMock() for field in fields(RiskSuggestionScope)})
    db.auth.resolve_principal.return_value = SimpleNamespace(user_id=7)
    db.projects.get_by_id.return_value = SimpleNamespace(
        key="PROJ", name="Проект", description_md="CRM"
    )
    db.tasks.get_by_project.return_value = [
        SimpleNamespace(
            id=44,
            number=12,
            title="Интеграция CRM",
            description_md="Ожидает согласования API",
            due_date=date(2026, 9, 12),
            stage_id=3,
        )
    ]
    db.stages.get_by_project.return_value = [SimpleNamespace(id=3, name="В работе")]
    for repo in (db.nodes, db.documents, db.risks, db.milestones, db.dependencies):
        repo.get_by_project.return_value = []
    db.comments.get_for_tasks.return_value = []
    db.activity.get_recent_by_project.return_value = []
    state = {"open": False, "calls": 0}

    @asynccontextmanager
    async def scope():
        state["open"] = True
        state["calls"] += 1
        try:
            yield db
        finally:
            state["open"] = False

    llm = AsyncMock()
    return RiskSuggestionService(scope=scope, llm_client=llm), db, llm, state


async def test_suggestion_releases_auth_and_data_connections_and_never_creates_risks():
    service, db, llm, state = setup_service()
    db.tasks.get_by_project.return_value[0].checklist = {
        "title": "Контракт",
        "items": [{"text": "Согласовать поля API", "is_completed": False}],
    }

    async def answer(**kwargs):
        assert state == {"open": False, "calls": 1}
        context = json.loads(kwargs["content"])
        assert "PROJ-12" in context["sources"]["S2"]
        assert "[ ] Согласовать поля API" in context["sources"]["S2"]
        return RiskSuggestionDraftSetSchema(suggestions=[draft()])

    llm.get_structured_response.side_effect = answer
    result = await service.suggest(project_id=1, session_token="session", bearer_secret=None)
    assert result.suggestions[0].task_id == 44
    assert "Ожидает согласования API" in result.suggestions[0].evidence[0]
    assert "source" not in result.suggestions[0].model_dump()
    db.auth.resolve_principal.assert_awaited_once_with(session_token="session", bearer_secret=None)
    db.access.ensure_project_access.assert_awaited_once_with(project_id=1, user_id=7)
    assert [call[0] for call in db.risks.mock_calls] == ["get_by_project"]


async def test_suggestions_drop_unverified_evidence_duplicates_and_foreign_task_links():
    service, db, llm, _ = setup_service()
    db.risks.get_by_project.return_value = [SimpleNamespace(title="Уже зарегистрирован")]
    base = draft()
    llm.get_structured_response.return_value = RiskSuggestionDraftSetSchema(
        suggestions=[
            {**base, "task_key": "OTHER-12"},
            {**base},
            {**base, "title": "Необоснованный", "evidence_refs": ["UNKNOWN"]},
            {**base, "title": "Уже зарегистрирован"},
        ]
    )
    result = await service.suggest(project_id=1, session_token=None, bearer_secret="read-token")
    assert len(result.suggestions) == 1
    assert result.suggestions[0].task_id is None


async def test_project_denial_precedes_reading_context_and_llm():
    service, db, llm, state = setup_service()
    db.access.ensure_project_access.side_effect = ResourceNotAvailableError(
        resource="project", resource_id=2
    )
    with pytest.raises(ResourceNotAvailableError):
        await service.suggest(project_id=2, session_token=None, bearer_secret="read-token")
    db.projects.get_by_id.assert_not_awaited()
    llm.get_structured_response.assert_not_awaited()
    assert not state["open"]


async def test_provider_failure_is_a_service_error_and_does_not_write():
    service, db, llm, state = setup_service()
    llm.get_structured_response.side_effect = LlmClientError("offline")
    with pytest.raises(AiProviderError):
        await service.suggest(project_id=1, session_token="session", bearer_secret=None)
    assert not state["open"]
    db.risks.save.assert_not_awaited()
