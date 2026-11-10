"""Связанный путь: личная доска, агент, проект, ИСР, перенос и найденный отчёт."""

# ruff: noqa: F811 -- pytest внедряет импортированную изолированную фикстуру.

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import func, select, text

from src.agent.aris import ArisMode
from src.agent.context import AgentContextBudget
from src.agent.runtime import ArisDeps, ArisRuntime, WriteGate
from src.agent.tools import AgentToolContext
from src.core.app_state import RUNTIME_STATE_KEY, SESSION_FACTORY_STATE_KEY, SETTINGS_STATE_KEY
from src.core.settings import get_settings
from src.db.models import AgentToolRun, BoardShape, Task, TaskReport
from src.db.models.api_tokens import ApiTokenScope
from src.dependencies.scopes import build_agent_project_tool_scope
from src.dependencies.services import ARIS_ACTIONS, build_project_tool_executor
from src.knowledge.catalog import SourceType
from src.mcp_server.server import read_project_knowledge
from src.mcp_server.services import build_moves_service, build_project_query_scope
from src.repositories.api_tokens import ApiTokensRepository
from src.schemas.boards import BoardCreateSchema
from src.schemas.enums import TaskKind
from src.schemas.knowledge import KnowledgeReadRequest
from src.schemas.task_transfers import TaskTransferTargetSchema
from src.services.agent_actions import AgentActionsService
from src.services.project_query import ProjectQueryService
from src.utils.api_tokens import generate_token_secret, hash_token_secret
from tests.integration.repositories.test_agent_conversations_repository import (
    agent_env as agent_env,
)
from tests.integration.repositories.test_agent_project_actions import result
from tests.integration.repositories.test_board_editor import promotion
from tests.integration.repositories.test_board_editor import service as board_editor_service
from tests.unit.knowledge.agent_models import final
from tests.unit.mcp_server.test_tool_context import TransportContext


async def run_action(executor, context, name, arguments, *, allow_write=True):
    """FunctionModel выбирает вызов, настоящий runtime ждёт настоящий WriteGate."""
    decision = asyncio.Event()
    results = []
    called = False

    async def permission():
        await decision.wait()
        return allow_write

    async def model(_messages, _info):
        nonlocal called
        if not called:
            called = True
            decision.set()
            return ModelResponse(parts=[ToolCallPart(name, arguments)])
        return final("Готово.")

    async def capture(_tool, response):
        results.append(response)

    gate = WriteGate(permission())
    deps = ArisDeps(
        execution=replace(context, request_id=uuid4()),
        executor=executor,
        system_prompt="Выполни ровно указанное действие.",
        content={"question": name},
        budget=AgentContextBudget(
            system_prompt="Выполни ровно указанное действие.",
            max_tokens=12000,
            history_tokens=1000,
        ),
        tool_rounds=2,
        on_result=capture,
        write_gate=gate,
    )
    try:
        completed = await ArisRuntime(model=FunctionModel(model), executor=executor).run(
            deps, metadata={"channel": "cross-domain-smoke"}
        )
        assert completed.output is not None
        if allow_write:
            assert len(results) == 1 and "error" not in results[0], results
            if "action" in results[0]:
                assert results[0]["action"]["status"] == "completed"
            return result(results[0])
        assert not results or all("error" in item for item in results)
        return None
    finally:
        gate.close()


async def test_personal_board_to_project_task_moves_and_report_search(agent_env, tmp_path):
    env = agent_env
    settings = get_settings().model_copy(deep=True)
    settings.app.uploads_path = tmp_path
    settings.knowledge.knowledge_enabled = False
    scope = build_agent_project_tool_scope(session_factory=env.factory, settings=settings)
    actions = AgentActionsService(scope=scope, actions=ARIS_ACTIONS)
    query = ProjectQueryService(scope=build_project_query_scope(env.factory))
    executor = build_project_tool_executor(query, actions)
    personal = AgentToolContext(
        project_id=None, user_id=1, can_write=True, mode=ArisMode.WIDGET_PERSONAL
    )

    async with scope() as db:
        board = await db.boards.create_free_board(
            data=BoardCreateSchema(title="Сквозная приёмка"), user_id=1
        )
        await db.unit_of_work.commit()
    diagram = {
        "board_id": board.id,
        "nodes": [{"key": "intake", "label": "Приёмка"}, {"key": "done", "label": "Итог"}],
        "edges": [{"source": {"type": "node", "key": "intake"},
                   "target": {"type": "node", "key": "done"}}],
    }
    await run_action(executor, personal, "telos_draw_personal_board_diagram", diagram, allow_write=False)
    async with env.factory() as session:
        assert await session.scalar(select(func.count()).select_from(BoardShape)) == 0
        assert await session.scalar(select(func.count()).select_from(AgentToolRun)) == 0
    drawn = await run_action(executor, personal, "telos_draw_personal_board_diagram", diagram)
    shape_id = drawn["board_change"]["batch"]["id_map"]["node_intake"]
    await run_action(executor, personal, "telos_update_personal_board_shapes", {
        "board_id": board.id,
        "updates": [{"id": shape_id, "revision": 1, "data": {"label": "Приёмка согласована"}}],
    })
    async with scope() as db:
        scene = await db.boards.get_free_content(board_id=board.id, user_id=1)
    assert len(scene.shapes) == 3
    edited = next(shape for shape in scene.shapes if shape.id == shape_id)
    assert edited.label == "Приёмка согласована" and edited.revision == 2

    async with env.factory() as session:
        # Общая фикстура задаёт проекты 1/2 явно; синхронизируем её serial перед promotion.
        await session.execute(text(
            "SELECT setval(pg_get_serial_sequence('projects', 'id'), (SELECT max(id) FROM projects))"
        ))
        promoted = await board_editor_service(session).promote(
            board_id=board.id, user_id=1,
            data=promotion(revision=scene.board.revision, key="SMOKE"),
        )
    project_id = promoted.project.id
    project = replace(personal, project_id=project_id, mode=ArisMode.WIDGET_PROJECT)
    async with scope() as db:
        scene = await db.boards.get_content(project_id=project_id, board_id=board.id)
    assert promoted.board.id == board.id and scene.board.project_id == project_id
    assert {shape.id for shape in scene.shapes} == {
        entry for entry in drawn["board_change"]["batch"]["id_map"].values()
    }
    assert next(shape for shape in scene.shapes if shape.id == shape_id).label == edited.label

    task = (await run_action(executor, project, "telos_create_task", {"title": "Проверить итог схемы"}))["task"]
    node = (await run_action(executor, project, "telos_create_wbs_node", {"title": "Приёмка результата"}))["node"]
    placed = await run_action(executor, project, "telos_place_task", {
        "task_id": task["id"], "wbs_node_id": node["id"], "before_task_id": None,
    })
    assert placed["task"]["wbs_node_id"] == node["id"]
    async with env.factory() as session:
        moves = build_moves_service(session, settings)
        preview = await moves.preview(task_id=task["id"], target=TaskTransferTargetSchema(personal=True), user_id=1)
        assert preview.losses.wbs_placements == 1
        moved = await moves.move(task_id=task["id"], target=TaskTransferTargetSchema(personal=True), user_id=1)
        assert moved.task.id == task["id"] and moved.task.kind is TaskKind.PERSONAL
        row = await session.get(Task, task["id"])
        assert (row.project_id, row.stage_id, row.wbs_node_id, row.wbs_position) == (None, None, None, None)
        returned = await moves.move(task_id=task["id"], target=TaskTransferTargetSchema(project_id=project_id), user_id=1)
        assert returned.task.id == task["id"] and returned.task.project_id == project_id
        assert returned.task.kind is TaskKind.PROJECT and returned.task.wbs_node_id is None
    async with scope() as db:
        structure = await db.wbs.get_structure(project_id)
    assert next(item for item in structure.tasks if item.id == task["id"]).wbs_node_id is None
    assert node["id"] in {item.id for item in structure.nodes}

    body = "Итог: алмазопроверка завершена, схема и задача прошли сквозную приёмку."
    published = await run_action(executor, project, "telos_publish_task_report", {
        "task_id": task["id"], "expected_revision": 0, "body_md": body,
    })
    read = await run_action(executor, project, "telos_get_task_report", {"task_id": task["id"]})
    listed = await run_action(executor, project, "telos_list_task_reports", {})
    assert published["report"]["revision"] == 1 and published["report"]["author_name"] == "1 Участник"
    assert read["text"] == body and read["source_id"] == published["source_id"]
    assert listed["items"][0]["task_id"] == task["id"] and listed["items"][0]["preview"] == body
    search = KnowledgeReadRequest(name="telos_search_sources", entity_type=SourceType.TASK_REPORT, query="алмазопроверка")
    hits = await query.read_knowledge(project_id=project_id, request=search)
    assert [item["source_id"] for item in hits["items"]] == [published["source_id"]]

    secret = generate_token_secret()
    async with env.factory() as session:
        await ApiTokensRepository(session).create(user_id=1, name="Smoke MCP", token_hash=hash_token_secret(secret),
            prefix=secret[:8], scope=ApiTokenScope.READ, expires_at=None)
        runs = list((await session.scalars(select(AgentToolRun))).all())
        report = await session.scalar(select(TaskReport).where(TaskReport.task_id == task["id"]))
    assert len(runs) == 6 and all(run.status == "completed" for run in runs)
    assert report.author_id == 1 and report.body_md == body
    context = TransportContext(state={RUNTIME_STATE_KEY: SimpleNamespace(), SETTINGS_STATE_KEY: settings,
        SESSION_FACTORY_STATE_KEY: env.factory}, headers={"Authorization": f"Bearer {secret}"})
    assert await read_project_knowledge(context, "SMOKE", search) == hits
    source = await read_project_knowledge(context, "SMOKE", KnowledgeReadRequest(
        name="telos_read_source", source_id=published["source_id"], max_chars=8000))
    assert source["entity_type"] == "task_report" and body in source["text"]
    env.llm.get_structured_response.assert_not_awaited()
