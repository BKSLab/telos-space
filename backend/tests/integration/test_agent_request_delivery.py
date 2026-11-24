"""Real PostgreSQL result polling: current owner/key, source ACL, remote proof and all metadata."""

from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update

from src.db.models.agent_messages import AgentMessage
from src.db.models.agent_tool_runs import AgentToolRun
from src.db.models.api_keys import ApiKey
from src.db.models.project_members import ProjectMember
from src.dependencies.scopes import build_agent_conversation_scope
from src.schemas.agent_requests import AgentAskSchema
from src.schemas.git_sources import GitSourceProof
from src.schemas.knowledge import (
    KnowledgeAnswerSchema,
    KnowledgeSourceSchema,
    encode_answer_sources,
)
from src.services.agent_requests import AgentRequestsService, RequestLinks
from src.services.auth import ApiKeyGrant
from src.services.context_evidence import (
    HIDDEN_CONTEXT_ANSWER,
    ContextEvidence,
    ContextEvidenceService,
)
from src.services.context_results import ContextReadResult
from tests.integration.repositories.test_git_sources import connect, environment


@pytest.mark.parametrize("revoke", ["membership", "source", "scope"])
async def test_external_poll_and_initial_wait_hide_all_source_data_after_current_access_changes(
    engine, revoke
):
    async with environment(engine) as env:
        env.remote.private = True
        connection = await connect(
            env,
            share_private_with_project=True,
        )
        read = await env.service.read_file(
            project_id=env.project,
            principal=env.member,
            connection_id=connection.id,
            path="README.md",
        )
        proof = GitSourceProof.model_validate(
            read.model_dump(include=set(GitSourceProof.model_fields))
        )
        key_id = uuid4()
        scopes = {"agent:ask", "projects:read", "knowledge:read"}
        caller = replace(
            env.member,
            via_api_token=True,
            api_key=ApiKeyGrant(
                key_id=key_id,
                label="Review",
                key_prefix="test",
                scopes=frozenset(scopes),
                owner_user_id=env.member.user_id,
            ),
        )
        async with env.sessions() as db:
            db.add(
                ApiKey(
                    id=key_id,
                    label="Result delivery",
                    key_hash="synthetic-hash",
                    key_prefix=uuid4().hex[:8],
                    owner_user_id=caller.user_id,
                    scopes=sorted(scopes),
                )
            )
            await db.commit()
        guard = ContextEvidenceService(
            scope=env.scope, task_scope=env.scope, documents=None, knowledge=None, git=env.service
        )
        ledger = ContextEvidence()
        ledger.register(ContextReadResult({}, project_id=env.project, git_proofs=(proof,)))
        body = "synthetic-private-answer-body"
        title = "synthetic-private-task-title"
        answer = KnowledgeAnswerSchema(
            answer=body,
            sources=[
                KnowledgeSourceSchema(
                    source_id=f"task:{env.tasks[0]}",
                    entity_type="task",
                    entity_id=env.tasks[0],
                    title=title,
                    card={"key": "PRIVATE-1"},
                )
            ],
        )
        answer._context_evidence = ledger, caller
        original_scope = build_agent_conversation_scope(session_factory=env.sessions)

        @asynccontextmanager
        async def request_scope():
            env.remote.db_active += 1
            try:
                async with original_scope() as current:
                    yield current
            finally:
                env.remote.db_active -= 1

        requests = AgentRequestsService(
            scope=request_scope, links=RequestLinks("https://review.test"), evidence=guard
        )
        request_id = await requests.ask_external(
            user_id=caller.user_id,
            api_key_id=key_id,
            key_scopes=sorted(scopes),
            data=AgentAskSchema(text="Review source", user_text="Review source"),
        )
        async with env.sessions() as db:
            message = await db.scalar(
                select(AgentMessage).where(
                    AgentMessage.request_id == request_id, AgentMessage.role == "assistant"
                )
            )
            message.status, message.content, message.sources, message.project_id = (
                "completed",
                body,
                encode_answer_sources(answer),
                env.project,
            )
            for status in ("completed", "pending", "rejected"):
                db.add(
                    AgentToolRun(
                        id=uuid4(),
                        project_id=env.project,
                        user_id=caller.user_id,
                        message_id=message.id,
                        request_id=uuid4(),
                        tool_name="telos_update_task",
                        title=f"synthetic-{status}-private-metadata",
                        arguments={"description_md": body},
                        status=status,
                        result={"task_key": "PRIVATE-1"},
                    )
                )
            await db.commit()
            message_id = message.id
        args = dict(
            user_id=caller.user_id, api_key_id=key_id, request_id=request_id, principal=caller
        )
        visible = await requests.get_external_result(**args)
        assert visible.answer == body and visible.links[0].title == title
        assert len(visible.done) == len(visible.pending) == len(visible.rejected) == 1
        assert visible.pending[0].arguments["description_md"] == body and visible.project_key
        if revoke == "source":
            env.remote.denied.add("app")
        else:
            async with env.sessions() as db:
                if revoke == "membership":
                    await db.execute(
                        delete(ProjectMember).where(
                            ProjectMember.project_id == env.project,
                            ProjectMember.user_id == caller.user_id,
                        )
                    )
                else:
                    await db.execute(
                        update(ApiKey)
                        .where(ApiKey.id == key_id)
                        .values(scopes=["agent:ask", "projects:read"])
                    )
                await db.commit()
        hidden = await requests.get_external_result(**args)
        waited = await requests.wait_external_result(**args, timeout_seconds=0)
        for result in (hidden, waited):
            assert result.status == "completed" and result.answer == HIDDEN_CONTEXT_ANSWER
            assert (
                result.project_key is None
                and not result.links
                and not result.done
                and not result.pending
                and not result.rejected
            )
            assert body not in result.model_dump_json() and title not in result.model_dump_json()
            assert (
                "PRIVATE-1" not in result.model_dump_json()
                and "private-metadata" not in result.model_dump_json()
            )
        async with env.sessions() as db:
            persisted = await db.get(AgentMessage, message_id)
            assert persisted.content == body and persisted.sources == encode_answer_sources(answer)
