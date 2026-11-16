"""Одна canonical source identity для опубликованной неизменяемой версии отчёта."""

# ruff: noqa: F811 -- pytest внедряет импортированные fixture по параметру.

from dataclasses import replace

from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import select

from src.agent.context import AgentContextBudget
from src.agent.runtime import ArisDeps, ArisRuntime
from src.db.models.task_reports import TaskReport
from src.dependencies.scopes import build_project_knowledge_query_scope
from src.dependencies.services import build_project_tool_executor
from src.knowledge.catalog import build_catalog
from src.knowledge.context import citation
from src.repositories.knowledge_sources import KnowledgeSourcesRepository
from src.schemas.knowledge import KnowledgeReadRequest
from src.services.project_query import ProjectQueryService
from tests.integration.repositories.test_agent_conversations_repository import (
    agent_env as agent_env,
)
from tests.integration.repositories.test_agent_project_actions import actions_env as actions_env
from tests.integration.repositories.test_agent_project_actions import invoke, task


async def test_report_publication_read_list_and_clear_use_immutable_report_identity(actions_env):
    env = actions_env
    created = await task(env, "Синтетическая заметка")
    task_id = created["id"]
    before = await invoke(env, "telos_get_task_report", {"task_id": task_id})
    first_body, second_body = "Первая версия результата", "Уточнённая вторая версия результата"
    first = (
        await invoke(
            env,
            "telos_publish_task_report",
            {"task_id": task_id, "expected_revision": 0, "body_md": first_body},
        )
    )["action"]["result"]
    second = (
        await invoke(
            env,
            "telos_publish_task_report",
            {"task_id": task_id, "expected_revision": 1, "body_md": second_body},
        )
    )["action"]["result"]
    current = await invoke(env, "telos_get_task_report", {"task_id": task_id})
    listing = await invoke(env, "telos_list_task_reports")
    async with env.factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(TaskReport)
                    .where(TaskReport.task_id == task_id)
                    .order_by(TaskReport.revision)
                )
            ).all()
        )
        first_id, second_id = (row.id for row in rows)
    assert first_id != second_id
    expected = (
        f"task_report:{first_id}",
        f"task_report:{second_id}",
        f"task_report:{second_id}",
        f"task_report:{second_id}",
    )
    actual = (
        first["source_id"],
        second["source_id"],
        current["source_id"],
        listing["items"][0]["source_id"],
    )
    assert actual == expected, {"actual": actual, "expected": expected, "task_id": task_id}
    assert (
        before["report"]["revision"] == 0
        and before["report"]["id"] is None
        and before["source_id"] is None
    )
    assert second["report"]["id"] == second_id and second["report"]["task_id"] == task_id
    assert current["report"]["id"] == listing["items"][0]["report"]["id"] == second_id
    query = ProjectQueryService(
        scope=build_project_knowledge_query_scope(session_factory=env.factory)
    )
    canonical = await query.read_knowledge(
        project_id=1,
        request=KnowledgeReadRequest(
            name="telos_read_source", source_id=second["source_id"], max_chars=8000
        ),
    )
    assert canonical["source_id"] == second["source_id"] and second_body in canonical["text"]
    assert first_body not in canonical["text"]
    async with env.factory() as session:
        catalog = build_catalog(1, await KnowledgeSourcesRepository(session).get_project_rows(1))
    reference = citation(catalog.sources[second["source_id"]], excerpt=second_body)
    assert (reference.source_id, reference.entity_id, reference.task_id) == (
        f"task_report:{second_id}",
        second_id,
        task_id,
    )
    # Очистка сохраняет все версии, но пустой источник не регистрируется как задача.
    cleared = (
        await invoke(
            env,
            "telos_publish_task_report",
            {"task_id": task_id, "expected_revision": 2, "body_md": ""},
        )
    )["action"]["result"]
    empty = await invoke(env, "telos_get_task_report", {"task_id": task_id})
    assert cleared["report"]["id"] is not None and cleared["report"]["revision"] == 3
    assert cleared["source_id"] is empty["source_id"] is None
    assert not (await invoke(env, "telos_list_task_reports"))["items"]
    historical = await query.read_knowledge(
        project_id=1,
        request=KnowledgeReadRequest(
            name="telos_read_source", source_id=f"task_report:{second_id}", max_chars=8000
        ),
    )
    assert second_body in historical["text"]
    async with env.factory() as session:
        history = list(
            (
                await session.scalars(
                    select(TaskReport)
                    .where(TaskReport.task_id == task_id)
                    .order_by(TaskReport.revision)
                )
            ).all()
        )
        assert [row.revision for row in history] == [1, 2, 3]
        assert [row.body_md for row in history] == [first_body, second_body, ""]


async def test_native_runtime_reads_the_same_second_report_version_via_catalog(actions_env):
    env = actions_env
    created = await task(env, "Нативное чтение отчёта")
    for revision, body in enumerate(("Прежний результат", "Новый точный результат")):
        await invoke(
            env,
            "telos_publish_task_report",
            {"task_id": created["id"], "expected_revision": revision, "body_md": body},
        )
    query = ProjectQueryService(
        scope=build_project_knowledge_query_scope(session_factory=env.factory)
    )
    executor = build_project_tool_executor(query, env.actions)
    step, source_id = 0, None

    async def respond(messages, info):
        nonlocal step, source_id
        step += 1
        if step == 1:
            return ModelResponse(
                parts=[ToolCallPart("telos_get_task_report", {"task_id": created["id"]})]
            )
        returned = [
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if step == 2:
            source_id = returned[-1]["source_id"]
            assert returned[-1]["report"]["revision"] == 2
            assert source_id == f"task_report:{returned[-1]['report']['id']}"
            return ModelResponse(
                parts=[
                    ToolCallPart("telos_read_source", {"source_id": source_id, "max_chars": 8000})
                ]
            )
        assert returned[-1]["source_id"] == source_id
        assert "Новый точный результат" in returned[-1]["text"]
        assert "Прежний результат" not in returned[-1]["text"]
        return ModelResponse(
            parts=[ToolCallPart("telos_submit_answer", {"answer": "Прочитана вторая публикация."})]
        )

    deps = ArisDeps(
        execution=replace(env.context, can_write=False),
        executor=executor,
        system_prompt="Прочитай конкретную публикацию отчёта через её канонический источник.",
        content={"question": "Что проверено?"},
        budget=AgentContextBudget(
            system_prompt="Прочитай отчёт", max_tokens=12000, history_tokens=1000
        ),
        tool_rounds=3,
    )
    result = await ArisRuntime(model=FunctionModel(respond), executor=executor).run(
        deps, metadata={"synthetic": True}
    )
    assert result.output.answer == "Прочитана вторая публикация."
    assert [call["tool"] for call in result.tool_calls] == [
        "telos_get_task_report",
        "telos_read_source",
    ]
