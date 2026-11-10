"""Сохранённая квитанция определяет повтор независимо от нового runtime и UUID seed."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, update

from src.agent.action_catalog import PROJECT_ACTIONS
from src.agent.tools import AgentToolContext
from src.clients.llm import LlmClient
from src.core.settings import get_settings
from src.db.models import AgentToolRun, ProjectMember, Task
from src.dependencies.scopes import (
    build_agent_conversation_scope,
    build_agent_project_tool_scope,
)
from src.exceptions.agent_tools import AgentToolAccessError
from src.schemas.agent_conversations import AgentMessageCreateSchema
from src.schemas.project_chats import ChatMessageCreate
from src.services.agent_actions import AgentActionsService
from src.services.agent_conversations import AgentConversationConfig, AgentConversationsService
from src.services.project_agent import ProjectAgentService


@pytest.mark.parametrize("origin", ["message", "chat"])
async def test_saved_receipt_survives_lost_response_restart_and_access_revocation(
    chat_env, tmp_path, origin
):
    env = chat_env
    settings = get_settings().model_copy(deep=True)
    settings.app.uploads_path = tmp_path
    scope = build_agent_project_tool_scope(session_factory=env.factory, settings=settings)
    actions = AgentActionsService(scope=scope, actions=PROJECT_ACTIONS)
    await actions.execute(
        context=AgentToolContext(project_id=1, user_id=1, can_write=True, request_id=uuid4()),
        name="telos_create_stage",
        arguments={"name": "Бэклог", "color": "#334455"},
    )
    if origin == "message":
        conversations = AgentConversationsService(
            scope=build_agent_conversation_scope(session_factory=env.factory),
            agent=AsyncMock(spec=ProjectAgentService),
            llm_client=AsyncMock(spec=LlmClient),
            config=AgentConversationConfig(
                turn_timeout_seconds=30, history_messages=4, summary_batch_size=2
            ),
        )
        conversation = await conversations.create_conversation(project_id=1, user_id=2)
        await conversations.send_message(
            project_id=1,
            user_id=2,
            conversation_id=conversation.id,
            data=AgentMessageCreateSchema(request_id=uuid4(), content="Создай задачу"),
        )
    else:
        await env.service.send(
            1, 2, ChatMessageCreate(content="Арис, создай задачу", client_message_id=uuid4())
        )
    run_id = uuid4()
    async with scope() as db:
        queue = db.messages if origin == "message" else db.turns
        answer = await queue.claim_next(run_id)
        await db.unit_of_work.commit()
    context = AgentToolContext(
        project_id=1,
        user_id=2,
        can_write=True,
        run_id=run_id,
        message_id=answer.id if origin == "message" else None,
        chat_turn_id=answer.id if origin == "chat" else None,
    )
    arguments = {"title": "Одна задача после потери ответа"}
    first = await actions.execute(context=context, name="telos_create_task", arguments=arguments)
    action_id = UUID(first["action"]["id"])
    # Непрозрачный UUID моделирует квитанцию, сохранённую до запуска нового runtime.
    # Уже зафиксированный эффект остаётся, хотя ответ вызова клиентом потерян.
    saved_request_id = UUID("bf4709b0-c14e-50fd-ae12-78a38df26b95")
    restarted_run = uuid4()
    async with env.factory() as db:
        await db.execute(
            update(AgentToolRun)
            .where(AgentToolRun.id == action_id)
            .values(request_id=saved_request_id)
        )
        await db.execute(
            update(type(answer)).where(type(answer).id == answer.id).values(run_id=restarted_run)
        )
        await db.commit()
    restarted = AgentActionsService(scope=scope, actions=PROJECT_ACTIONS)
    context = replace(context, run_id=restarted_run)
    repeated = await asyncio.gather(
        *[
            restarted.execute(context=context, name="telos_create_task", arguments=arguments)
            for _ in range(2)
        ]
    )
    assert all(item == first for item in repeated)
    async with env.factory() as db:
        tasks = (await db.scalars(select(Task).where(Task.title == arguments["title"]))).all()
        receipts = (
            await db.scalars(
                select(AgentToolRun).where(AgentToolRun.tool_name == "telos_create_task")
            )
        ).all()
        assert len(tasks) == len(receipts) == 1
        assert receipts[0].id == action_id and receipts[0].request_id == saved_request_id
        await db.execute(
            delete(ProjectMember).where(ProjectMember.project_id == 1, ProjectMember.user_id == 2)
        )
        await db.commit()
    with pytest.raises(AgentToolAccessError):
        await restarted.execute(context=context, name="telos_create_task", arguments=arguments)
    async with env.factory() as db:
        assert len((await db.scalars(select(Task))).all()) == 1
        assert (await db.get(AgentToolRun, action_id)).request_id == saved_request_id
