"""Exact consent topic is validated before its durable pending receipt."""

# ruff: noqa: F811 -- pytest injects the re-exported fixtures by name.

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from src.db.models.agent_tool_runs import AgentToolRun
from src.exceptions.agent_tools import AgentToolArgumentsError
from src.services.context_evidence import ContextEvidenceService
from tests.integration.repositories.test_agent_conversations_repository import (
    agent_env as agent_env,
)
from tests.integration.repositories.test_agent_project_actions import actions_env as actions_env
from tests.integration.repositories.test_agent_project_actions import invoke
from tests.integration.repositories.test_context_actions import context_resources


async def test_invalid_web_topics_never_create_pending_or_reach_approval(actions_env):
    env = actions_env
    _, client, _ = context_resources(env)
    actor = replace(
        env.context,
        user_id=2,
        can_write=False,
        principal=ContextEvidenceService.session_principal(2),
    )
    for invalid in ("   ", "public\x00topic", "public\ntopic", "word " * 76, "x" * 601):
        with pytest.raises(AgentToolArgumentsError) as error:
            await invoke(env, "telos_request_web_search", {"public_topic": invalid}, context=actor)
        assert error.value.status_code == 422
        async with env.factory() as db:
            assert await db.scalar(select(func.count()).select_from(AgentToolRun)) == 0
    client.read.assert_not_awaited()
    client.search.assert_not_awaited()


@pytest.mark.parametrize("topic", [" ".join(f"word{i:02}" for i in range(75)), "x" * 600])
async def test_valid_web_topic_pending_approval_and_replay_share_exact_canonical_payload(
    actions_env, topic
):
    env = actions_env
    _, client, signer = context_resources(env)
    actor = replace(
        env.context,
        user_id=2,
        can_write=False,
        principal=ContextEvidenceService.session_principal(2),
    )
    request_id = uuid4()
    # The character limit applies to input before normalization, as in the HTTP
    # schema. The 600-character boundary therefore uses its exact original text.
    raw = f"  {topic}  " if len(topic) < 596 else topic
    response = await invoke(
        env,
        "telos_request_web_search",
        {"public_topic": raw},
        request_id=request_id,
        context=actor,
    )
    pending = response["action"]
    assert pending["status"] == "pending" and pending["effect"] == "external_release"
    assert pending["arguments"] == {"public_topic": topic}
    assert "topic_token" not in str(pending)
    async with env.factory() as db:
        row = await db.get(AgentToolRun, UUID(pending["id"]))
        assert row.arguments == pending["arguments"]
    approved = await env.actions.decide(
        context=actor, action_id=UUID(pending["id"]), decision="approve"
    )
    assert approved.result["public_topic"] == topic
    payload = signer.verify(approved.result["topic_token"], project_id=1, user_id=2, kind="topic")
    assert payload["q"] == pending["arguments"]["public_topic"]
    assert payload["a"] == pending["id"]
    replay = await invoke(
        env,
        "telos_request_web_search",
        {"public_topic": topic},
        request_id=request_id,
        context=actor,
    )
    assert replay["action"]["id"] == pending["id"]
    assert replay["action"]["status"] == "completed"
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(AgentToolRun)) == 1
    client.read.assert_not_awaited()
    client.search.assert_not_awaited()
