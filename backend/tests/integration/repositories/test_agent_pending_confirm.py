"""Подтверждение действия агента по ссылке ?confirm=<id> (#40793) на PostgreSQL."""

# ruff: noqa: F811 -- pytest внедряет импортированные фикстуры по имени параметра.

from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, update

from src.db.models import AgentToolRun, ProjectMember
from src.exceptions.agent_tools import (
    AgentToolAccessError,
    AgentToolDecidedError,
    AgentToolExpiredError,
)
from src.schemas.knowledge import KnowledgeAnswerSchema
from tests.integration.repositories.test_agent_personal_conversations import (
    agent_env as agent_env,
)
from tests.integration.repositories.test_agent_personal_conversations import read, send, start
from tests.integration.repositories.test_agent_project_actions import actions_env as actions_env
from tests.integration.repositories.test_agent_project_actions import invoke, task


async def test_external_pending_cannot_be_approved_through_widget_decision(actions_env):
    from src.agent.tools import AgentToolContext
    from src.db.models.agent_conversations import AgentConversation
    from src.db.models.agent_messages import AgentMessage

    env = actions_env
    original = await task(env)
    action_id = await propose_delete(env, original)
    async with env.factory() as session:
        conversation = AgentConversation(user_id=1, project_id=None, title="Внешний API", channel="api")
        session.add(conversation)
        await session.flush()
        message = AgentMessage(conversation_id=conversation.id, request_id=uuid4(),
            role="assistant", content="Требуется решение", status="completed")
        session.add(message)
        await session.flush()
        row = await session.get(AgentToolRun, action_id)
        row.message_id = message.id
        await session.commit()
    with pytest.raises(AgentToolAccessError):
        await env.actions.decide(context=AgentToolContext(project_id=None, user_id=1, can_write=True),
            action_id=action_id, decision="approve", conversation_id=conversation.id)
    assert (await invoke(env, "telos_list_tasks"))["total"] == 1
    decision = await env.actions.decide_pending(user_id=1, action_id=action_id, decision="approve")
    assert decision.status == "completed"
    assert (await invoke(env, "telos_list_tasks"))["total"] == 0


async def propose_delete(env, original, *, context=None):
    response = await invoke(
        env,
        "telos_delete_task",
        {"task_id": original["id"], "expected_updated_at": original["updated_at"]},
        context=context,
    )
    assert response["action"]["status"] == "pending"
    return UUID(response["action"]["id"])


async def test_only_initiator_sees_and_decides_and_repeat_is_an_error(actions_env):
    env = actions_env
    original = await task(env)
    action_id = await propose_delete(env, original)

    view = await env.actions.get_pending(user_id=1, action_id=action_id)
    assert view.status == "pending" and view.project.key == "P1"
    assert view.preview["task"]["title"] == original["title"]
    assert view.arguments["task_id"] == original["id"] and view.result == {}
    assert view.expires_at - view.created_at == env.actions.confirm_ttl

    # Участник того же проекта, но не инициатор: действие для него не существует.
    for call in (
        env.actions.get_pending(user_id=2, action_id=action_id),
        env.actions.decide_pending(user_id=2, action_id=action_id, decision="approve"),
        env.actions.get_pending(user_id=1, action_id=uuid4()),
    ):
        with pytest.raises(AgentToolAccessError):
            await call
    assert (await invoke(env, "telos_list_tasks"))["total"] == 1

    done = await env.actions.decide_pending(user_id=1, action_id=action_id, decision="approve")
    assert done.status == "completed" and done.result["participant_decision"] == "approve"
    assert (await invoke(env, "telos_list_tasks"))["total"] == 0
    for decision in ("approve", "reject"):
        with pytest.raises(AgentToolDecidedError):
            await env.actions.decide_pending(user_id=1, action_id=action_id, decision=decision)


async def test_reject_changes_nothing_and_blocks_later_approve(actions_env):
    env = actions_env
    original = await task(env)
    action_id = await propose_delete(env, original)
    rejected = await env.actions.decide_pending(user_id=1, action_id=action_id, decision="reject")
    assert rejected.status == "rejected"
    assert (await invoke(env, "telos_list_tasks"))["total"] == 1
    with pytest.raises(AgentToolDecidedError):
        await env.actions.decide_pending(user_id=1, action_id=action_id, decision="approve")


async def test_expired_action_cannot_be_approved_but_can_be_rejected(actions_env):
    env = actions_env
    original = await task(env)
    action_id = await propose_delete(env, original)
    async with env.factory() as session:
        await session.execute(
            update(AgentToolRun)
            .where(AgentToolRun.id == action_id)
            .values(created_at=AgentToolRun.created_at - env.actions.confirm_ttl - timedelta(1))
        )
        await session.commit()
    assert (await env.actions.get_pending(user_id=1, action_id=action_id)).status == "expired"
    with pytest.raises(AgentToolExpiredError):
        await env.actions.decide_pending(user_id=1, action_id=action_id, decision="approve")
    # Истечение срока закрывает и путь из чата/MCP, а не только ссылку.
    with pytest.raises(AgentToolExpiredError):
        await env.actions.decide(context=env.context, action_id=action_id, decision="approve")
    assert (await invoke(env, "telos_list_tasks"))["total"] == 1
    closed = await env.actions.decide_pending(user_id=1, action_id=action_id, decision="reject")
    assert closed.status == "rejected"


async def test_initiator_without_project_access_gets_not_found(actions_env):
    env = actions_env
    original = await task(env)
    action_id = await propose_delete(env, original, context=replace(env.context, user_id=2))
    assert (await env.actions.get_pending(user_id=2, action_id=action_id)).status == "pending"
    async with env.factory() as session:
        await session.execute(
            delete(ProjectMember).where(ProjectMember.project_id == 1, ProjectMember.user_id == 2)
        )
        await session.commit()
    with pytest.raises(AgentToolAccessError):
        await env.actions.get_pending(user_id=2, action_id=action_id)
    with pytest.raises(AgentToolAccessError):
        await env.actions.decide_pending(user_id=2, action_id=action_id, decision="approve")
    assert (await invoke(env, "telos_list_tasks"))["total"] == 1


async def test_link_decision_of_chat_action_finds_conversation_and_resumes_answer(actions_env):
    env = actions_env
    original = await task(env)
    conversation = await start(env)
    calls = []

    async def respond(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            await env.actions.execute(
                context=kwargs["execution"],
                name="telos_delete_task",
                arguments={
                    "task_id": original["id"],
                    "expected_updated_at": original["updated_at"],
                },
            )
            return KnowledgeAnswerSchema(answer="Подтвердите удаление по ссылке.", sources=[])
        return KnowledgeAnswerSchema(answer="Задача удалена.", sources=[])

    env.responder.ask.side_effect = respond
    await send(env, conversation.id, "Удали задачу")
    await env.service.process_next()
    action = (await read(env, conversation.id)).items[-1].actions[0]
    done = await env.actions.decide_pending(user_id=1, action_id=action.id, decision="approve")
    assert done.status == "completed"
    assert (await read(env, conversation.id)).items[-1].status == "queued"
    await env.service.process_next()
    assert calls[1]["action_history"][0]["status"] == "completed"
