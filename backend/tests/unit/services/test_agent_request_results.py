"""External polling must never replay guarded body or metadata without current proof."""

from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.exceptions.agent_conversations import AgentRequestNotFoundError
from src.services.agent_requests import AgentRequestsService, RequestLinks
from src.services.auth import ApiKeyGrant
from src.services.context_evidence import HIDDEN_CONTEXT_ANSWER


def result_environment(principal):
    key_id = principal.api_key.key_id if principal.api_key else None
    answer = SimpleNamespace(
        id=10,
        conversation_id=20,
        request_id=uuid4(),
        project_id=7,
        status="completed",
        content="synthetic-private-body",
        sources=[
            {
                "reference_type": "context_guard",
                "guard": {"projects": [7], "restricted_projects": [7]},
            }
        ],
        error=None,
        progress=None,
    )
    question = SimpleNamespace(role="user", verbatim="User request")
    conversation = SimpleNamespace(
        id=20, channel="api", user_id=principal.user_id, api_key_id=key_id, external_key="review"
    )
    run = SimpleNamespace(
        id=uuid4(),
        tool_name="telos_update_task",
        title="Private task metadata",
        status="pending",
        arguments={"description_md": "synthetic-private-body"},
        result={"task_key": "PRIVATE-1"},
        project_id=7,
    )
    db = SimpleNamespace(
        auth=SimpleNamespace(refresh_principal=AsyncMock(return_value=principal)),
        access=SimpleNamespace(ensure_project_access=AsyncMock()),
        messages=SimpleNamespace(
            get_answer_by_request=AsyncMock(return_value=answer),
            get_request=AsyncMock(return_value=[question]),
        ),
        conversations=SimpleNamespace(get_by_id=AsyncMock(return_value=conversation)),
        runs=SimpleNamespace(get_for_messages=AsyncMock(return_value=[run])),
        projects=SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(key="PRIVATE"))),
    )
    active = 0

    @asynccontextmanager
    async def scope():
        nonlocal active
        active += 1
        try:
            yield db
        finally:
            active -= 1

    async def present(_values, *, user_id, principal):
        assert active == 0, "Remote source validation must not hold the request DB scope"
        assert user_id == principal.user_id
        return {"sources": [], "external_sources": [], "assertion_references": []}, True

    evidence = SimpleNamespace(present=AsyncMock(side_effect=present))

    async def present_many(pages, **kwargs):
        return [await evidence.present(values, **kwargs) for values in pages]

    evidence.present_many = present_many
    service = AgentRequestsService(scope=scope, links=RequestLinks("https://review.test"))
    service.evidence = evidence
    return SimpleNamespace(
        service=service, db=db, evidence=evidence, answer=answer, key_id=key_id, principal=principal
    )


def call(env, **kwargs):
    return dict(
        user_id=env.principal.user_id,
        api_key_id=env.key_id,
        request_id=env.answer.request_id,
        **kwargs,
    )


async def test_missing_reader_or_failed_source_proof_never_returns_body_or_action_metadata(
    current_principal,
):
    env = result_environment(current_principal)
    result = await env.service.get_external_result(**call(env))
    assert result.answer == HIDDEN_CONTEXT_ANSWER
    assert (
        result.project_key is None
        and not result.links
        and not result.pending
        and not result.done
        and not result.rejected
    )
    assert "synthetic-private-body" not in result.model_dump_json()
    env.evidence.present.side_effect = None
    env.evidence.present.return_value = (
        {"sources": [], "external_sources": [], "assertion_references": []},
        False,
    )
    result = await env.service.get_external_result(**call(env, principal=current_principal))
    assert (
        result.answer == HIDDEN_CONTEXT_ANSWER and not result.pending and result.project_key is None
    )
    env.evidence.present.assert_awaited_once_with(
        env.answer.sources, user_id=current_principal.user_id, principal=current_principal
    )


async def test_current_reader_is_forwarded_after_scope_closes_and_wait_revalidates(
    current_principal,
):
    env = result_environment(current_principal)
    result = await env.service.wait_external_result(
        **call(env, principal=current_principal), timeout_seconds=0
    )
    assert result.answer == env.answer.content and result.pending[0].arguments == {
        "description_md": "synthetic-private-body"
    }
    assert result.project_key == "PRIVATE" and env.evidence.present.await_count == 1
    assert env.evidence.present.await_args.kwargs["principal"] is current_principal


async def test_reader_identity_and_key_binding_are_not_reconstructed_from_owner_id(
    current_principal,
):
    env = result_environment(current_principal)
    other = replace(current_principal, user_id=current_principal.user_id + 1)
    key = replace(
        current_principal,
        via_api_token=True,
        api_key=ApiKeyGrant(
            key_id=uuid4(),
            label="Other credential",
            key_prefix="test",
            scopes=frozenset({"agent:ask"}),
            owner_user_id=current_principal.user_id,
        ),
    )
    for principal in (other, key):
        with pytest.raises(AgentRequestNotFoundError):
            await env.service.get_external_result(**call(env, principal=principal))
    env.evidence.present.assert_not_awaited()


async def test_unavailable_evidence_service_fails_closed_for_guarded_answer(current_principal):
    env = result_environment(current_principal)
    env.service.evidence = None
    result = await env.service.get_external_result(**call(env, principal=current_principal))
    assert (
        result.answer == HIDDEN_CONTEXT_ANSWER and result.project_key is None and not result.pending
    )


async def test_plain_unsourced_status_keeps_its_public_shape(current_principal):
    env = result_environment(current_principal)
    env.answer.sources, env.answer.project_id, env.answer.content = [], None, "Hello"
    env.db.runs.get_for_messages.return_value = []
    result = await env.service.get_external_result(**call(env))
    assert result.answer == "Hello" and result.status == "completed" and result.write_allowed
    env.evidence.present.assert_not_awaited()


async def test_unfinished_answer_never_releases_action_inputs_or_private_progress_before_guard(
    current_principal,
):
    env = result_environment(current_principal)
    env.answer.sources = []
    for status in ("queued", "processing", "failed"):
        env.answer.status, env.answer.content, env.answer.progress = (
            status,
            "",
            "synthetic-private-source-path",
        )
        result = await env.service.wait_external_result(
            **call(env, principal=current_principal), timeout_seconds=0
        )
        assert result.status == status and result.answer is None and result.progress is None
        assert (
            not result.pending
            and not result.done
            and not result.rejected
            and result.project_key is None
        )
        assert "synthetic-private" not in result.model_dump_json()
