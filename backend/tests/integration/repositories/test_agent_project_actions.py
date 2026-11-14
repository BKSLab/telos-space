"""Полные проектные инструменты на PostgreSQL, включая реальные commit сервисов."""

# ruff: noqa: F811 -- pytest внедряет импортированные фикстуры по имени параметра.

import asyncio
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select

from src.agent.action_catalog import PROJECT_ACTIONS
from src.agent.tools import AgentToolContext
from src.core.settings import get_settings
from src.db.models import AgentToolRun, Task
from src.dependencies.scopes import build_agent_project_tool_scope
from src.exceptions.agent_tools import (
    AgentToolAccessError,
    AgentToolConflictError,
    AgentToolOperationError,
)
from src.services.agent_actions import AgentActionsService
from tests.integration.repositories.test_agent_conversations_repository import (
    agent_env as agent_env,
)


@pytest_asyncio.fixture
async def actions_env(agent_env, tmp_path):
    settings = get_settings().model_copy(deep=True)
    settings.app.uploads_path = tmp_path
    service = AgentActionsService(
        scope=build_agent_project_tool_scope(session_factory=agent_env.factory, settings=settings),
        actions=PROJECT_ACTIONS,
    )
    context = AgentToolContext(project_id=1, user_id=1, can_write=True)
    agent_env.actions = service
    agent_env.context = context
    agent_env.files = tmp_path
    return agent_env


async def invoke(env, name, arguments=None, *, request_id=None, context=None):
    return await env.actions.execute(
        context=replace(context or env.context, request_id=request_id or uuid4()),
        name=name,
        arguments=arguments or {},
    )


def result(response):
    return response["action"]["result"] if "action" in response else response


async def task(env, title="Проверить окно заказа"):
    stages = (await invoke(env, "telos_list_stages"))["items"]
    if not stages:
        await invoke(env, "telos_create_stage", {"name": "Бэклог", "color": "#334455"})
    return result(await invoke(env, "telos_create_task", {"title": title}))["task"]


async def approve(env, response, *, context=None):
    action = response["action"]
    assert action["status"] == "pending"
    return await env.actions.decide(
        context=context or env.context, action_id=UUID(action["id"]), decision="approve"
    )


async def test_canonical_actions_reject_former_names_without_writes_or_receipts(actions_env):
    env = actions_env
    for old_name in ("create_task", "tt_create_task", "get_project", "tt_get_project"):
        with pytest.raises(AgentToolConflictError, match="отсутствует в реестре"):
            await invoke(env, old_name, {"title": "Не создавать"})
    assert (await invoke(env, "telos_list_tasks"))["total"] == 0
    async with env.factory() as session:
        assert not (await session.scalars(select(AgentToolRun))).all()


async def test_canonical_runtime_write_gate_receipts_and_confirmation_on_postgres(actions_env):
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    from src.agent.context import AgentContextBudget
    from src.agent.runtime import ArisDeps, ArisRuntime, WriteGate
    from src.dependencies.scopes import build_project_knowledge_query_scope
    from src.dependencies.services import build_project_tool_executor
    from src.services.project_query import ProjectQueryService

    env = actions_env
    await invoke(env, "telos_create_stage", {"name": "Бэклог", "color": "#334455"})
    executor = build_project_tool_executor(
        ProjectQueryService(
            scope=build_project_knowledge_query_scope(session_factory=env.factory)
        ),
        env.actions,
    )

    async def run(name, arguments, request_id, *, write_allowed):
        step = 0
        responses = []

        async def decide_write():
            return write_allowed

        async def remember(_tool, result):
            responses.append(result)

        async def respond(_messages, info):
            nonlocal step
            step += 1
            assert all(tool.name.startswith("telos_") for tool in info.function_tools)
            if step == 1:
                return ModelResponse(
                    parts=[ToolCallPart("telos_load_capability", {"id": "tasks"})]
                )
            if step == 2:
                return ModelResponse(parts=[ToolCallPart(name, arguments)])
            return ModelResponse(
                parts=[ToolCallPart("telos_submit_answer", {"answer": "Проверено."})]
            )

        deps = ArisDeps(
            execution=replace(env.context, request_id=request_id),
            executor=executor,
            system_prompt="Проверь поручение инструментами.",
            content={"question": "Выполни поручение."},
            budget=AgentContextBudget(
                system_prompt="Проверь поручение инструментами.",
                max_tokens=12000,
                history_tokens=1000,
            ),
            tool_rounds=4,
            defer_workflows=True,
            write_gate_factory=lambda: WriteGate(decide_write()),
            on_result=remember,
        )
        try:
            result = await ArisRuntime(model=FunctionModel(respond), executor=executor).run(
                deps, metadata={}
            )
            assert result.output.answer == "Проверено."
            return result, responses
        finally:
            if deps.write_gate is not None:
                deps.write_gate.close()

    request_id = uuid4()
    denied, responses = await run(
        "telos_create_task", {"title": "Из канонического runtime"}, request_id,
        write_allowed=False,
    )
    assert not responses
    assert denied.tool_calls[-1]["refused"] == "write_intent"
    assert (await invoke(env, "telos_list_tasks"))["total"] == 0

    _, created = await run(
        "telos_create_task", {"title": "Из канонического runtime"}, request_id,
        write_allowed=True,
    )
    _, repeated = await run(
        "telos_create_task", {"title": "Из канонического runtime"}, request_id,
        write_allowed=True,
    )
    assert created == repeated
    assert created[0]["action"]["tool_name"] == "telos_create_task"
    assert (await invoke(env, "telos_list_tasks"))["total"] == 1
    original = created[0]["action"]["result"]["task"]
    _, proposed = await run(
        "telos_delete_task",
        {"task_id": original["id"], "expected_updated_at": original["updated_at"]},
        uuid4(), write_allowed=True,
    )
    assert proposed[0]["action"]["tool_name"] == "telos_delete_task"
    assert proposed[0]["action"]["status"] == "pending"
    assert (await invoke(env, "telos_get_task", {"task_id": original["id"]}))["task"]["id"] == original["id"]
    completed = await approve(env, proposed[0])
    assert completed.status == "completed" and completed.tool_name == "telos_delete_task"
    assert (await invoke(env, "telos_list_tasks"))["total"] == 0


async def test_tasks_assignments_checklist_comments_and_deletion(actions_env):
    env = actions_env
    original = await task(env)
    task_id = original["id"]
    updated = result(
        await invoke(
            env,
            "telos_update_task",
            {
                "task_id": task_id,
                "changes": {
                    "title": "Проверка формы",
                    "executor_id": 2,
                    "observer_ids": [1],
                    "start_date": "2026-09-21",
                    "due_date": "2026-09-25",
                },
            },
        )
    )["task"]
    assert updated["title"] == "Проверка формы"
    assert any(
        item["user"]["id"] == 2 and item["role"] == "EXECUTOR" for item in updated["participants"]
    )
    assert (await invoke(env, "telos_list_tasks", {"search": "Проверка формы"}))["total"] == 1
    checklist = result(
        await invoke(
            env,
            "telos_set_checklist",
            {
                "task_id": task_id,
                "checklist_revision": 0,
                "checklist": {"items": [{"text": "Открыть форму"}]},
            },
        )
    )
    item = checklist["checklist"]["items"][0]
    item["is_completed"] = True
    checked = result(
        await invoke(
            env,
            "telos_set_checklist",
            {
                "task_id": task_id,
                "checklist_revision": checklist["checklist_revision"],
                "checklist": {"items": [item]},
            },
        )
    )
    assert checked["checklist"]["items"][0]["is_completed"]
    with pytest.raises(AgentToolOperationError):
        await invoke(
            env, "telos_set_checklist", {"task_id": task_id, "checklist_revision": 0, "checklist": None}
        )
    stage = result(await invoke(env, "telos_create_stage", {"name": "Готово", "is_done_stage": True}))[
        "stage"
    ]
    moved = result(await invoke(env, "telos_move_task", {"task_id": task_id, "stage_id": stage["id"]}))[
        "task"
    ]
    assert moved["stage_id"] == stage["id"]
    comment = result(
        await invoke(env, "telos_add_comment", {"task_id": task_id, "body_md": "Проверено"})
    )["comment"]
    edited = result(
        await invoke(
            env,
            "telos_update_comment",
            {
                "comment_id": comment["id"],
                "body_md": "Проверено дважды",
                "expected_body_md": "Проверено",
            },
        )
    )["comment"]
    assert (
        edited["author_name"] == comment["author_name"] and edited["body_md"] == "Проверено дважды"
    )
    with pytest.raises(AgentToolOperationError):
        await invoke(
            env,
            "telos_update_comment",
            {
                "comment_id": comment["id"],
                "body_md": "Устаревшая правка",
                "expected_body_md": "Проверено",
            },
        )
    assert (await invoke(env, "telos_list_comments", {"task_id": task_id}))["total"] == 1
    await approve(env, await invoke(env, "telos_delete_comment", {"comment_id": comment["id"]}))
    assert not (await invoke(env, "telos_list_comments", {"task_id": task_id}))["items"]
    await approve(env, await invoke(env, "telos_fix_task_baseline", {"task_id": task_id}))
    fresh = (await invoke(env, "telos_get_task", {"task_id": task_id}))["task"]
    deletion = await invoke(
        env, "telos_delete_task", {"task_id": task_id, "expected_updated_at": fresh["updated_at"]}
    )
    assert (await invoke(env, "telos_get_task", {"task_id": task_id}))["task"]["id"] == task_id
    done = await approve(env, deletion)
    assert done.status == "completed"
    assert await approve(env, deletion) == done
    assert not (await invoke(env, "telos_list_tasks"))["items"]


async def test_project_team_stickers_and_roles(actions_env):
    env = actions_env
    assert (await invoke(env, "telos_get_project"))["project"]["id"] == 1
    assert (
        result(await invoke(env, "telos_update_project", {"name": "Пилот"}))["project"]["name"] == "Пилот"
    )
    member = replace(env.context, user_id=2)
    with pytest.raises(AgentToolAccessError):
        await invoke(env, "telos_update_project", {"name": "Запрещено"}, context=member)
    original = await task(env)
    sticker = result(
        await invoke(env, "telos_create_sticker", {"body": "Проверить UX", "task_ids": [original["id"]]})
    )["sticker"]
    revised = result(
        await invoke(
            env,
            "telos_update_sticker",
            {
                "sticker_id": sticker["id"],
                "changes": {"revision": sticker["revision"], "body": "Обсудить UX"},
            },
        )
    )["sticker"]
    assert revised["body"] == "Обсудить UX"
    await invoke(
        env,
        "telos_move_sticker",
        {"sticker_id": sticker["id"], "position": {"canvas_x": 150, "canvas_y": 200}},
    )
    current = (await invoke(env, "telos_list_stickers"))["items"][0]
    assert current["canvas_x"] == 150
    await approve(
        env,
        await invoke(
            env, "telos_delete_sticker", {"sticker_id": current["id"], "revision": current["revision"]}
        ),
    )
    assert not (await invoke(env, "telos_list_stickers"))["items"]
    await approve(env, await invoke(env, "telos_remove_member", {"user_id": 2}))
    assert (await invoke(env, "telos_list_members"))["total"] == 1
    await invoke(env, "telos_add_member", {"username": "user2"})
    transfer = await invoke(env, "telos_transfer_project_ownership", {"user_id": 2})
    with pytest.raises(AgentToolAccessError):
        await approve(env, transfer, context=member)
    await approve(env, transfer)
    assert (await invoke(env, "telos_get_project"))["project"]["owner_id"] == 2
    with pytest.raises(AgentToolAccessError):
        await invoke(env, "telos_remove_member", {"user_id": 2})


async def test_documents_links_and_binary_attachment_copy(actions_env):
    env = actions_env
    first, second = await task(env, "Первая"), await task(env, "Вторая")
    document = result(
        await invoke(env, "telos_create_document", {"title": "Приёмка", "content_md": "Исходный план"})
    )["document"]
    document_ref = document["ref"]
    await invoke(
        env,
        "telos_update_document",
        {"document_ref": document_ref, "changes": {"content_md": "Проверить форму заказа", "expected_revision": document["revision"]}},
    )
    assert (await invoke(env, "telos_list_documents"))["total"] == 1
    await invoke(env, "telos_link_document", {"document_ref": document_ref, "task_id": first["id"]})
    read = await invoke(env, "telos_get_document", {"document_ref": document_ref})
    assert read["text"] == "Проверить форму заказа" and len(read["document"]["tasks"]) == 1
    await invoke(env, "telos_unlink_document", {"document_ref": document_ref, "task_id": first["id"]})
    assert not (await invoke(env, "telos_get_document", {"document_ref": document_ref}))["document"]["tasks"]
    attached = result(
        await invoke(
            env,
            "telos_create_text_attachment",
            {"task_id": first["id"], "file_name": "plan.md", "content": "Проверить форму заказа"},
        )
    )["document"]
    copied = result(
        await invoke(
            env,
            "telos_copy_document",
            {
                "document_ref": attached["ref"],
                "target_task_id": second["id"],
            },
        )
    )["document"]
    assert copied["ref"] != attached["ref"] and copied["original"]["size"] == attached["original"]["size"]
    assert len((await invoke(env, "telos_list_documents", {"task_id": second["id"], "source_kind": "upload"}))["items"]) == 1
    await approve(
        env,
        await invoke(
            env, "telos_delete_document", {"document_ref": attached["ref"]}
        ),
    )
    assert not (await invoke(env, "telos_list_documents", {"task_id": first["id"], "source_kind": "upload"}))["items"]
    assert len(list(env.files.rglob("*.md"))) == 1
    await approve(env, await invoke(env, "telos_delete_document", {"document_ref": document_ref}))
    assert [item["ref"] for item in (await invoke(env, "telos_list_documents"))["items"]] == [copied["ref"]]


async def test_document_actions_keep_owner_scopes_and_recheck_completed_receipts(actions_env):
    from sqlalchemy import update

    from src.db.models.api_keys import ApiKey
    from src.utils.api_keys import generate_api_key, hash_api_key

    env = actions_env
    owner_task = await task(env)
    project_doc = result(await invoke(env, "telos_create_document", {"title": "Проектный документ", "content_md": "Исходный текст"}))["document"]
    task_doc = result(await invoke(env, "telos_create_document", {"title": "Документ задачи", "owner_task_id": owner_task["id"], "content_md": "Текст задачи"}))["document"]

    async def actor(scopes):
        raw, prefix = generate_api_key()
        async with env.factory() as db:
            key = ApiKey(label="Synthetic action key", key_hash=hash_api_key(raw), key_prefix=prefix, kind="personal", owner_user_id=1, scopes=list(scopes))
            db.add(key)
            await db.flush()
            key_id = key.id
            await db.commit()
        async with env.actions.scope() as db:
            principal = await db.auth.resolve_principal(session_token=None, bearer_secret=raw)
        return replace(env.context, principal=principal), key_id

    project_writer, _ = await actor({"projects:read", "projects:write"})
    task_writer, task_key_id = await actor({"projects:read", "tasks:write"})
    def change(document):
        return {"document_ref": document["ref"], "changes": {"expected_revision": document["revision"], "content_md": "Проверено"}}
    with pytest.raises(AgentToolOperationError):
        await invoke(env, "telos_update_document", change(task_doc), context=project_writer)
    with pytest.raises(AgentToolOperationError):
        await invoke(env, "telos_update_document", change(project_doc), context=task_writer)
    for context in (project_writer, task_writer):
        with pytest.raises(AgentToolOperationError):
            await invoke(env, "telos_link_document", {"document_ref": project_doc["ref"], "task_id": owner_task["id"]}, context=context)
    request_id = uuid4()
    changed = await invoke(env, "telos_update_document", change(task_doc), context=task_writer, request_id=request_id)
    assert result(changed)["document"]["revision"] > task_doc["revision"]
    assert await invoke(env, "telos_update_document", change(task_doc), context=task_writer, request_id=request_id) == changed
    copied = result(await invoke(env, "telos_copy_document", {"document_ref": project_doc["ref"], "target_task_id": owner_task["id"]}, context=task_writer))["document"]
    assert copied["source_kind"] == "text" and copied["ownership"]["owner_task_id"] == owner_task["id"]
    async with env.factory() as db:
        await db.execute(update(ApiKey).where(ApiKey.id == task_key_id).values(scopes=["projects:read"]))
        await db.commit()
    with pytest.raises(AgentToolAccessError):
        await invoke(env, "telos_update_document", change(task_doc), context=task_writer, request_id=request_id)
    assert (await invoke(env, "telos_get_document", {"document_ref": project_doc["ref"]}))["text"] == "Исходный текст"


async def test_personal_document_actions_keep_personal_and_shared_project_contexts_separate(actions_env):
    import json
    from unittest.mock import AsyncMock

    from src.agent.action_catalog import PERSONAL_ACTIONS
    from src.agent.aris import ArisMode
    from src.agent.tools import AgentToolRequest
    from src.dependencies.services import build_project_tool_executor
    from src.services.project_query import ProjectQueryService
    from tests.unit.knowledge.agent_models import LegacyOutput
    from tests.unit.knowledge.test_project_agent import build_service

    env = actions_env
    env.actions = AgentActionsService(scope=env.actions.scope, actions=PROJECT_ACTIONS + PERSONAL_ACTIONS)
    personal = replace(env.context, project_id=None, mode=ArisMode.WIDGET_PERSONAL)
    personal_task = result(await invoke(env, "telos_create_personal_task", {"title": "Личная задача"}, context=personal))["task"]
    project_doc = result(await invoke(env, "telos_create_document", {"title": "Проектный текст", "content_md": "Командные данные"}))["document"]
    private_doc = result(await invoke(env, "telos_create_document", {"title": "Личный текст", "owner_task_id": personal_task["id"], "content_md": "Приватные условия"}, context=personal))["document"]
    private_ref = private_doc["ref"]
    page = await invoke(env, "telos_list_documents", context=personal)
    assert page["total"] == 1 and [item["ref"] for item in page["items"]] == [private_ref]
    assert (await invoke(env, "telos_get_document", {"document_ref": private_ref}, context=personal))["text"] == "Приватные условия"
    with pytest.raises(AgentToolAccessError):
        await invoke(env, "telos_get_document", {"document_ref": project_doc["ref"]}, context=personal)
    with pytest.raises(AgentToolAccessError):
        await invoke(env, "telos_get_document", {"document_ref": private_ref}, context=replace(env.context, mode=ArisMode.PROJECT_CHAT))
    with pytest.raises(AgentToolConflictError):
        await invoke(env, "telos_create_document", {"title": "Нет личной задачи"}, context=personal)
    service, _, clients, source_db = build_service()
    service.tools = build_project_tool_executor(AsyncMock(spec=ProjectQueryService), env.actions)

    async def respond(*, content, **_kwargs):
        payload = json.loads(content)
        if not payload.get("read_results"):
            return LegacyOutput(tool_calls=[AgentToolRequest(name="telos_get_document", arguments={"document_ref": private_ref})])
        page = payload["read_results"][0]["result"]
        return LegacyOutput(answer="Личные условия проверены.", source_ids=[page["source_handle"]], primary_reference=page["fragment_handle"])

    clients.llm_client.get_structured_response.side_effect = respond
    answer = await service.ask_personal(question="Что сказано в личном документе?", history=[], memory="", actor={"user_id": 1}, execution=replace(personal, can_write=False))
    assert len(answer.sources) == 1 and answer.sources[0].document_ref == private_ref
    assert answer.sources[0].is_primary and answer.sources[0].reference.quote == "Приватные условия"
    source_db.sources.get_project_rows.assert_not_awaited()
    clients.embedding_client.get_embedding.assert_not_awaited()
    clients.qdrant_client.search.assert_not_awaited()


async def test_wbs_milestones_dependencies_calendar_and_stages(actions_env):
    env = actions_env
    first, second = await task(env, "Спроектировать"), await task(env, "Проверить")
    stage = result(await invoke(env, "telos_create_stage", {"name": "Проверка"}))["stage"]
    await invoke(env, "telos_update_stage", {"stage_id": stage["id"], "changes": {"name": "Приёмка"}})
    root = result(await invoke(env, "telos_create_wbs_node", {"title": "Заказы"}))["node"]
    child = result(
        await invoke(env, "telos_create_wbs_node", {"title": "Интерфейс", "parent_id": root["id"]})
    )["node"]
    await invoke(env, "telos_update_wbs_node", {"node_id": child["id"], "title": "Форма"})
    await invoke(env, "telos_move_wbs_node", {"node_id": child["id"], "parent_id": None})
    await invoke(env, "telos_place_task", {"task_id": first["id"], "wbs_node_id": child["id"]})
    structure = await invoke(env, "telos_get_structure")
    assert len(structure["nodes"]) == 2 and structure["stats"]["assigned_tasks"] == 1
    milestone = result(
        await invoke(
            env,
            "telos_create_milestone",
            {"title": "Демо", "due_date": "2026-09-30", "wbs_node_id": child["id"]},
        )
    )["milestone"]
    await invoke(
        env,
        "telos_update_milestone",
        {"milestone_id": milestone["id"], "changes": {"title": "Демо формы"}},
    )
    assert (await invoke(env, "telos_list_milestones"))["total"] == 1
    dependency = result(
        await invoke(
            env,
            "telos_create_dependency",
            {"predecessor_task_id": first["id"], "successor_task_id": second["id"]},
        )
    )["dependency"]
    assert (await invoke(env, "telos_list_dependencies"))["total"] == 1
    changes = [{"task_id": first["id"], "start_date": "2026-09-21", "due_date": "2026-09-25"}]
    preview = await invoke(env, "telos_preview_schedule_change", {"changes": changes})
    assert preview
    fresh = (await invoke(env, "telos_get_task", {"task_id": first["id"]}))["task"]
    await approve(
        env,
        await invoke(
            env,
            "telos_apply_schedule_change",
            {"changes": [{**changes[0], "expected_updated_at": fresh["updated_at"]}]},
        ),
    )
    assert (await invoke(env, "telos_get_task", {"task_id": first["id"]}))["task"][
        "due_date"
    ] == "2026-09-25"
    assert await invoke(env, "telos_get_calendar", {"date_from": "2026-09-01", "date_to": "2026-10-31"})
    await approve(env, await invoke(env, "telos_delete_dependency", {"dependency_id": dependency["id"]}))
    await approve(env, await invoke(env, "telos_delete_milestone", {"milestone_id": milestone["id"]}))
    await approve(env, await invoke(env, "telos_delete_wbs_node", {"node_id": child["id"]}))
    assert (await invoke(env, "telos_get_structure"))["stats"]["assigned_tasks"] == 0
    await approve(env, await invoke(env, "telos_delete_stage", {"stage_id": stage["id"]}))


async def test_risks_cross_project_isolation_and_stale_deletion(actions_env):
    env = actions_env
    original = await task(env)
    foreign_ctx = replace(env.context, project_id=2)
    await invoke(env, "telos_create_stage", {"name": "Чужой бэклог"}, context=foreign_ctx)
    foreign = result(
        await invoke(env, "telos_create_task", {"title": "Чужая задача"}, context=foreign_ctx)
    )["task"]
    for name, arguments in [
        ("telos_get_task", {"task_id": foreign["id"]}),
        ("telos_update_task", {"task_id": foreign["id"], "changes": {"title": "Нельзя"}}),
        ("telos_add_comment", {"task_id": foreign["id"], "body_md": "Нельзя"}),
        ("telos_delete_task", {"task_id": foreign["id"], "expected_updated_at": foreign["updated_at"]}),
    ]:
        with pytest.raises(AgentToolAccessError):
            await invoke(env, name, arguments)
    risk = result(
        await invoke(
            env,
            "telos_create_risk",
            {
                "title": "Срыв проверки",
                "description": "Не хватает времени на проверку",
                "probability": "HIGH",
                "impact": "HIGH",
                "response_strategy": "MITIGATE",
                "task_id": original["id"],
                "owner_user_id": 2,
            },
        )
    )["risk"]
    assert (await invoke(env, "telos_get_risk", {"risk_id": risk["id"]}))["risk"]["task_id"] == original[
        "id"
    ]
    await invoke(
        env,
        "telos_update_risk",
        {"risk_id": risk["id"], "changes": {"status": "CLOSED", "mitigation_plan": "Проверено"}},
    )
    assert not (await invoke(env, "telos_list_risks", {"active_only": True}))["items"]
    await approve(env, await invoke(env, "telos_delete_risk", {"risk_id": risk["id"]}))
    deletion = await invoke(
        env,
        "telos_delete_task",
        {"task_id": original["id"], "expected_updated_at": original["updated_at"]},
    )
    await invoke(
        env, "telos_update_task", {"task_id": original["id"], "changes": {"title": "Уже изменена"}}
    )
    with pytest.raises((AgentToolConflictError, AgentToolOperationError)):
        await approve(env, deletion)
    rejected = await env.actions.decide(
        context=env.context, action_id=UUID(deletion["action"]["id"]), decision="reject"
    )
    assert rejected.status == "rejected"
    assert (await invoke(env, "telos_get_task", {"task_id": original["id"]}))["task"][
        "title"
    ] == "Уже изменена"


async def test_action_commit_and_result_are_atomic_and_concurrent_retry_creates_one_task(
    actions_env,
):
    env = actions_env
    await invoke(env, "telos_create_stage", {"name": "Бэклог", "color": "#334455"})
    request_id = uuid4()
    first, duplicate = await asyncio.gather(
        invoke(env, "telos_create_task", {"title": "Одна задача"}, request_id=request_id),
        invoke(env, "telos_create_task", {"title": "Одна задача"}, request_id=request_id),
    )
    assert first == duplicate
    async with env.factory() as session:
        assert len((await session.scalars(select(Task))).all()) == 1
        assert (
            len(
                (
                    await session.scalars(
                        select(AgentToolRun).where(AgentToolRun.tool_name == "telos_create_task")
                    )
                ).all()
            )
            == 1
        )
    with pytest.raises(AgentToolConflictError):
        await invoke(env, "telos_create_task", {"title": "Другой текст"}, request_id=request_id)
    with pytest.raises(AgentToolAccessError):
        await invoke(
            env,
            "telos_create_task",
            {"title": "Запрещено"},
            context=replace(env.context, can_write=False),
        )


async def test_failed_result_write_rolls_back_domain_commits_and_new_file(actions_env, monkeypatch):
    env = actions_env
    original = await task(env)
    from src.exceptions.agent_tools import AgentToolRunsRepositoryError, AgentToolsServiceError
    from src.repositories.agent_tool_runs import AgentToolRunsRepository

    async def fail(self, data):
        raise AgentToolRunsRepositoryError("Ошибка журнала после доменного commit")

    monkeypatch.setattr(AgentToolRunsRepository, "save", fail)
    with pytest.raises(AgentToolsServiceError):
        await invoke(env, "telos_create_task", {"title": "Не должна сохраниться"})
    with pytest.raises(AgentToolsServiceError):
        await invoke(
            env,
            "telos_create_text_attachment",
            {"task_id": original["id"], "file_name": "test.txt", "content": "Тест"},
        )
    async with env.factory() as session:
        assert len((await session.scalars(select(Task))).all()) == 1
    assert not list(env.files.rglob("*.txt"))
