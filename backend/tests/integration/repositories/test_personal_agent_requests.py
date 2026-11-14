"""Внешняя очередь остаётся локальной: владелец, UUID и безопасный повтор."""

# ruff: noqa: F811

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.db.models.api_keys import ApiKey
from src.db.models.api_tokens import ApiToken, ApiTokenScope
from src.exceptions.agent_conversations import AgentMessageConflictError, AgentRequestNotFoundError
from src.schemas.agent_requests import AgentAskSchema
from src.schemas.knowledge import KnowledgeAnswerSchema
from src.services.agent_requests import AgentRequestsService, RequestLinks, principal_key_scopes
from src.services.agent_routing import AgentRoutingAnswer, AgentRoutingService
from src.utils.api_tokens import build_display_prefix, generate_token_secret, hash_token_secret
from tests.integration.repositories.test_agent_personal_conversations import agent_env as agent_env


def requests(env):
    return AgentRequestsService(scope=env.service.scope, links=RequestLinks("http://routing.test"))


async def test_parallel_retries_share_one_external_request(agent_env):
    env = agent_env
    service = requests(env)
    data = AgentAskSchema(
        request_id=uuid4(), text="Что у меня в работе?", conversation_id="local:chat:1"
    )
    ids = await asyncio.gather(
        *[
            service.ask_external(user_id=1, api_key_id=None, data=data, key_scopes=None)
            for _ in range(2)
        ]
    )
    assert ids == [data.request_id, data.request_id]
    async with env.service.scope() as db:
        conversation = await db.conversations.get_external(
            user_id=1, api_key_id=None, external_key=data.conversation_id
        )
        pair = await db.messages.get_request(conversation.id, data.request_id)
    assert [message.role for message in pair] == ["user", "assistant"]
    with pytest.raises(AgentMessageConflictError):
        await service.ask_external(
            user_id=1,
            api_key_id=None,
            data=data.model_copy(update={"text": "Другой вопрос"}),
            key_scopes=None,
        )


async def test_same_uuid_in_another_owner_scope_never_hides_or_reveals_a_result(agent_env):
    env = agent_env
    service = requests(env)
    data = AgentAskSchema(
        request_id=uuid4(), text="Что у меня в работе?", conversation_id="local:chat:1"
    )
    await service.ask_external(user_id=1, api_key_id=None, data=data, key_scopes=None)
    with pytest.raises(AgentRequestNotFoundError):
        await service.get_external_result(user_id=2, api_key_id=None, request_id=data.request_id)
    raw = generate_token_secret()
    async with env.factory() as db:
        token = ApiToken(
            user_id=2,
            name="Synthetic read token",
            token_hash=hash_token_secret(raw),
            prefix=build_display_prefix(raw),
            scope=ApiTokenScope.READ,
        )
        db.add(token)
        await db.flush()
        token_id = token.id
        await db.commit()
    await service.ask_external(
        user_id=2,
        api_key_id=None,
        data=data.model_copy(update={"text": "Мои сроки"}),
        key_scopes=[],
        api_token_id=token_id,
    )
    routing = AsyncMock(spec=AgentRoutingService)
    routing.ask.return_value = AgentRoutingAnswer(
        answer=KnowledgeAnswerSchema(answer="Проверено.", sources=[]),
        project_id=None,
        project_key=None,
        mode="overview",
    )
    env.service.routing = routing
    assert await env.service.process_next()
    assert await env.service.process_next()
    for user_id in (1, 2):
        result = await service.get_external_result(
            user_id=user_id, api_key_id=None, request_id=data.request_id
        )
        assert result.status == "completed" and result.answer == "Проверено."
    assert [call.kwargs["user_id"] for call in routing.ask.await_args_list] == [1, 2]
    assert routing.ask.await_args_list[1].kwargs["execution"].allowed_tools == frozenset()


async def test_key_revoked_before_queue_claim_does_not_run_the_model(agent_env):
    env = agent_env
    key_id = uuid4()
    async with env.factory() as session:
        session.add(
            ApiKey(
                id=key_id,
                key_prefix=uuid4().hex[:12],
                key_hash="test-hash",
                label="Личный",
                owner_user_id=1,
                kind="personal",
                scopes=["agent:ask", "tasks:write"],
            )
        )
        await session.commit()
    service = requests(env)
    request_id = await service.ask_external(
        user_id=1,
        api_key_id=key_id,
        data=AgentAskSchema(text="Создай задачу", user_text="Создай задачу"),
        key_scopes=["agent:ask", "tasks:write"],
    )
    async with env.factory() as session:
        key = await session.get(ApiKey, key_id)
        key.is_active = False
        await session.commit()
    routing = AsyncMock(spec=AgentRoutingService)
    env.service.routing = routing
    assert await env.service.process_next()
    routing.ask.assert_not_awaited()
    result = await service.get_external_result(user_id=1, api_key_id=key_id, request_id=request_id)
    assert result.status == "failed"


@pytest.mark.parametrize("change", ["revoke", "expire", "delete", "read"])
async def test_legacy_token_current_credentials_are_checked_before_claim(agent_env, change):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import delete, update

    env = agent_env
    raw = generate_token_secret()
    async with env.factory() as db:
        token = ApiToken(
            user_id=1,
            name="Synthetic write token",
            token_hash=hash_token_secret(raw),
            prefix=build_display_prefix(raw),
            scope=ApiTokenScope.WRITE,
        )
        db.add(token)
        await db.flush()
        token_id = token.id
        await db.commit()
    async with env.service.scope() as db:
        principal = await db.auth.resolve_principal(session_token=None, bearer_secret=raw)
    service = requests(env)
    request_id = await service.ask_external(
        user_id=1,
        api_key_id=None,
        api_token_id=principal.api_token_id,
        data=AgentAskSchema(text="Создай личную задачу", user_text="Создай личную задачу"),
        key_scopes=principal_key_scopes(principal),
    )
    async with env.factory() as db:
        if change == "delete":
            await db.execute(delete(ApiToken).where(ApiToken.id == token_id))
        else:
            values = (
                {"scope": ApiTokenScope.READ}
                if change == "read"
                else {"revoked_at": datetime.now(UTC)}
                if change == "revoke"
                else {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
            )
            await db.execute(update(ApiToken).where(ApiToken.id == token_id).values(**values))
        await db.commit()
    routing = AsyncMock(spec=AgentRoutingService)
    routing.ask.return_value = AgentRoutingAnswer(
        answer=KnowledgeAnswerSchema(answer="Проверено без записи.", sources=[]),
        project_id=None,
        project_key=None,
        mode="overview",
    )
    env.service.routing = routing
    assert await env.service.process_next()
    result = await service.get_external_result(user_id=1, api_key_id=None, request_id=request_id)
    if change == "read":
        execution = routing.ask.await_args.kwargs["execution"]
        assert not execution.can_write and execution.principal.scope is ApiTokenScope.READ
        assert result.status == "completed"
    else:
        routing.ask.assert_not_awaited()
        assert result.status == "failed"
