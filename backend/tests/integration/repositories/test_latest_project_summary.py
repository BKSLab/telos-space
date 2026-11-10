"""Сохранённый анализ проекта читается без генерации и с актуальным доступом."""

# ruff: noqa: F811 -- pytest внедряет импортированную фикстуру по имени параметра.

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.agent.action_catalog import PROJECT_ACTIONS
from src.agent.action_scopes import tools_for_scopes
from src.agent.tools import AgentToolContext
from src.clients.llm import LlmClient
from src.core.settings import get_settings
from src.db.models.agent_tool_runs import AgentToolRun
from src.db.models.analytics_reports import AnalyticsReport
from src.db.models.project_members import ProjectMember
from src.dependencies.scopes import build_agent_project_tool_scope, build_analytics_scope
from src.exceptions.agent_tools import (
    AgentToolAccessError,
    AgentToolArgumentsError,
    AgentToolConflictError,
)
from src.exceptions.projects import ProjectNotFoundError
from src.repositories.analytics_reports import AnalyticsReportsRepository
from src.repositories.unit_of_work import AgentToolUnitOfWork
from src.schemas import project_tool_inputs as inputs
from src.services.agent_actions import AgentActionsService
from src.services.agent_project_actions import get_latest_project_summary
from src.services.analytics import AnalyticsReportReader, AnalyticsService
from src.utils.api_scopes import SCOPE_PROJECTS_READ, SCOPE_TASKS_READ
from tests.integration.repositories.test_agent_conversations_repository import (
    agent_env as agent_env,
)

SAVED_AT = datetime(2026, 11, 4, 8, 30, tzinfo=UTC)
ACTION_NAME = "telos_get_latest_project_summary"


def report_data(*, project_id: int | None = 1, headline: str = "Сохранённый анализ") -> dict:
    """Полный синтетический свод с текстами, превышающими компактную карточку."""
    task = {
        "id": 501,
        "key": "P1-501",
        "title": "Синтетическая задача из сохранённого анализа",
        "project_key": "P1",
        "due_date": "2026-10-31",
        "is_overdue": True,
    }
    common = {"project_key": "P1", "project_name": "Проект 1", "tasks": [task]}
    return {
        "project_id": project_id,
        "created_by_user_id": 1,
        "created_by_display_name_snapshot": "Автор сохранённого анализа",
        "llm_model": "synthetic-saved-model",
        "duration_ms": 1234,
        "created_at": SAVED_AT,
        "payload": {
            "headline": headline,
            "health": "WATCH",
            "health_note": "Основание сохранённой оценки. " * 40,
            "findings": [
                {
                    **common,
                    "kind": "OVERDUE",
                    "severity": "HIGH",
                    "title": f"Находка {index}",
                    "detail": f"Полное подтверждение {index}. " * 70,
                }
                for index in range(3)
            ],
            "progress": [
                {
                    **common,
                    "title": f"Результат {index}",
                    "detail": f"Полное описание результата {index}. " * 60,
                }
                for index in range(3)
            ],
            "recommendations": [
                {
                    **common,
                    "horizon": "TODAY",
                    "title": f"Рекомендация {index}",
                    "detail": f"Полное обоснование рекомендации {index}. " * 50,
                }
                for index in range(3)
            ],
            "signals": {
                "total_tasks": 10,
                "done_tasks": 4,
                "overdue_tasks": 1,
                "due_soon_tasks": 2,
                "no_due_date_tasks": 1,
                "unassigned_tasks": 0,
                "stale_tasks": 1,
                "blocked_tasks": 0,
                "unplaced_tasks": 2,
                "milestones_at_risk": 0,
            },
        },
        "context_summary": {
            "projects": 1,
            "risks_total": 2,
            "risks_included": 1,
            "tasks_total": 10,
            "tasks_included": 8,
            "comments_included": 3,
            "documents_included": 1,
            "stickers_included": 2,
            "wbs_nodes_included": 4,
            "milestones_included": 1,
            "activity_included": 8,
            "entity_counts": {"attachments": {"total": 12, "included": 10}},
            "truncated": True,
            "omitted": ["За пределами сохранённого анализа осталось две задачи."],
        },
    }


@pytest_asyncio.fixture
async def summary_env(agent_env, tmp_path):
    """Собирает настоящий граф инструмента без клиентов генерации."""
    settings = get_settings().model_copy(deep=True)
    settings.app.uploads_path = tmp_path
    settings.knowledge.knowledge_enabled = False
    scope = build_agent_project_tool_scope(session_factory=agent_env.factory, settings=settings)
    return SimpleNamespace(
        factory=agent_env.factory,
        settings=settings,
        scope=scope,
        service=AgentActionsService(scope=scope, actions=PROJECT_ACTIONS),
    )


async def save_report(env, **overrides) -> AnalyticsReport:
    """Фиксирует синтетический свод до начала read-only сценария."""
    data = report_data()
    data.update(overrides)
    async with env.factory() as session:
        row = AnalyticsReport(**data)
        session.add(row)
        await session.commit()
        return row


async def read_summary(env, *, user_id: int = 2, project_id: int | None = 1, arguments=None):
    """Вызывает каталог действий с read-only серверным контекстом."""
    return await env.service.execute(
        context=AgentToolContext(
            user_id=user_id,
            project_id=project_id,
            can_write=False,
            allowed_tools=tools_for_scopes([SCOPE_PROJECTS_READ]),
        ),
        name=ACTION_NAME,
        arguments={} if arguments is None else arguments,
    )


async def test_latest_is_teamwide_full_and_read_only_without_model_clients(
    summary_env, monkeypatch
):
    env = summary_env
    await save_report(env, payload={**report_data()["payload"], "headline": "Первый в ту же дату"})
    expected = await save_report(env)
    await save_report(env, created_at=SAVED_AT - timedelta(days=1))
    await save_report(
        env, **{**report_data(project_id=None), "created_at": SAVED_AT + timedelta(days=1)}
    )
    await save_report(
        env, **{**report_data(project_id=2), "created_at": SAVED_AT + timedelta(days=2)}
    )

    def unexpected_client(*args, **kwargs):
        pytest.fail("Чтение сохранённого анализа не должно создавать LLM/HTTP-клиенты.")

    monkeypatch.setattr(LlmClient, "__init__", unexpected_client)
    monkeypatch.setattr(httpx.AsyncClient, "__init__", unexpected_client)
    generate = AsyncMock(spec=AnalyticsService.generate, side_effect=AssertionError("generation"))
    commit = AsyncMock(spec=AgentToolUnitOfWork.commit, side_effect=AssertionError("commit"))
    save = AsyncMock(spec=AnalyticsReportsRepository.save, side_effect=AssertionError("save"))
    monkeypatch.setattr(AnalyticsService, "generate", generate)
    monkeypatch.setattr(AgentToolUnitOfWork, "commit", commit)
    monkeypatch.setattr(AnalyticsReportsRepository, "save", save)
    # И сборка нового scope, и само чтение происходят с запрещёнными конструкторами.
    env.scope = build_agent_project_tool_scope(session_factory=env.factory, settings=env.settings)
    env.service = AgentActionsService(scope=env.scope, actions=PROJECT_ACTIONS)

    result = await read_summary(env)

    assert result["found"] is True
    assert result["source_id"] == f"analytics_report:{expected.id}"
    assert result["provenance"] == "generated_analysis"
    assert "Сохранённый анализ на дату" in result["message"]
    assert SAVED_AT.isoformat() in result["message"]
    report = result["report"]
    assert report["id"] == expected.id
    assert report["project_id"] == 1
    assert report["project_key"] == "P1"
    assert report["project_name"] == "Проект 1"
    assert report["scope"] == "PROJECT"
    assert report["created_by"] == expected.created_by_display_name_snapshot
    assert report["llm_model"] == expected.llm_model
    assert report["duration_ms"] == expected.duration_ms
    for field in ("headline", "health", "health_note", "findings", "progress", "recommendations"):
        assert report[field] == expected.payload[field]
    assert report["context"] == expected.context_summary
    assert {
        field: report["signals"][field] for field in expected.payload["signals"]
    } == expected.payload["signals"]
    owner_result = await read_summary(env, user_id=1)
    assert owner_result == result
    reader = AnalyticsReportReader(scope=build_analytics_scope(session_factory=env.factory))
    assert not hasattr(reader, "llm_client")
    assert AnalyticsService.get_latest is AnalyticsReportReader.get_latest
    assert result["report"] == (await reader.get_latest(user_id=2, project_id=1)).model_dump(
        mode="json"
    )
    generate.assert_not_awaited()
    commit.assert_not_awaited()
    save.assert_not_awaited()
    async with env.factory() as session:
        assert await session.scalar(select(func.count()).select_from(AnalyticsReport)) == 5
        assert await session.scalar(select(func.count()).select_from(AgentToolRun)) == 0


async def test_missing_project_summary_never_falls_back_to_portfolio(summary_env):
    env = summary_env
    await save_report(env, **report_data(project_id=None, headline="Приватный портфель"))
    await save_report(env, **report_data(project_id=2, headline="Другой проект"))

    result = await read_summary(env)

    assert result == {
        "found": False,
        "report": None,
        "source_id": None,
        "provenance": "generated_analysis",
        "message": "Сохранённой сводки проекта нет: анализ ещё не формировали.",
    }
    with pytest.raises(AgentToolConflictError):
        await read_summary(env, project_id=None)
    async with env.scope() as db:
        with pytest.raises(AgentToolConflictError):
            await get_latest_project_summary(
                db, AgentToolContext(user_id=2, project_id=None), inputs.EmptyInput()
            )


async def test_inaccessible_project_and_revoked_membership_are_404(summary_env):
    env = summary_env
    await save_report(env)
    await save_report(env, **report_data(project_id=2))

    with pytest.raises(AgentToolAccessError) as foreign:
        await read_summary(env, project_id=2)
    assert foreign.value.status_code == 404
    assert (await read_summary(env))["found"] is True
    async with env.factory() as session:
        await session.execute(
            delete(ProjectMember).where(ProjectMember.project_id == 1, ProjectMember.user_id == 2)
        )
        await session.commit()

    with pytest.raises(AgentToolAccessError) as revoked:
        await read_summary(env)
    assert revoked.value.status_code == 404
    # Общее чтение перепроверяет членство даже при прямом вызове handler/HTTP reader.
    reader = AnalyticsReportReader(scope=build_analytics_scope(session_factory=env.factory))
    with pytest.raises(ProjectNotFoundError) as reader_error:
        await reader.get_latest(user_id=2, project_id=1)
    assert reader_error.value.status_code == 404
    async with env.scope() as db:
        with pytest.raises(ProjectNotFoundError):
            await get_latest_project_summary(
                db, AgentToolContext(user_id=2, project_id=1), inputs.EmptyInput()
            )


async def test_summary_input_cannot_override_server_project_or_select_portfolio(summary_env):
    env = summary_env
    definition = next(item for item in PROJECT_ACTIONS if item.name == ACTION_NAME)
    assert definition.parameters is inputs.EmptyInput
    assert not definition.mutating
    assert not definition.requires_confirmation
    assert definition.needs_project
    assert ACTION_NAME in tools_for_scopes([SCOPE_PROJECTS_READ])
    assert ACTION_NAME not in tools_for_scopes([SCOPE_TASKS_READ])
    for arguments in ({"project_id": 2}, {"project_id": None}, {"scope": "PORTFOLIO"}):
        with pytest.raises(AgentToolArgumentsError):
            await read_summary(env, arguments=arguments)


async def test_full_actions_read_uses_one_connection_and_releases_it(agent_env, summary_env):
    """Одинарный пул обслуживает отсутствие, полный свод и параллельные чтения."""
    engine = create_async_engine(
        agent_env.engine.url, pool_size=1, max_overflow=0, pool_timeout=0.25
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    scope = build_agent_project_tool_scope(session_factory=factory, settings=summary_env.settings)
    env = SimpleNamespace(
        factory=factory, service=AgentActionsService(scope=scope, actions=PROJECT_ACTIONS)
    )
    try:
        missing = await read_summary(env)
        assert missing["found"] is False
        assert engine.pool.checkedout() == 0

        saved = await save_report(env)
        present = await read_summary(env)
        assert present["found"] is True
        assert present["report"]["id"] == saved.id
        assert engine.pool.checkedout() == 0

        async with asyncio.timeout(5):
            results = await asyncio.gather(
                read_summary(env, user_id=1), read_summary(env, user_id=2)
            )
        assert results == [present, present]
        assert engine.pool.checkedout() == 0
        async with engine.connect() as connection:
            assert await connection.scalar(select(1)) == 1
        assert engine.pool.checkedout() == 0
    finally:
        await engine.dispose()
