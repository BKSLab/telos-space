"""Один проект: три одноимённых файла, два PR, актуальные факты и final ACL.

PostgreSQL, GitSourceService, selection и native ARIS настоящие. Provider GET
и FunctionModel управляемые; платных запросов и живого GitHub здесь нет.
"""

from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import func, select

from src.agent.action_catalog import PROJECT_ACTIONS
from src.agent.tools import AgentToolContext
from src.clients.llm import LlmClient
from src.core.settings import get_settings
from src.db.models.git_sources import GitChange
from src.db.models.project_stages import ProjectStage
from src.db.models.tasks import Task
from src.dependencies.agent_context import build_agent_context_tools
from src.dependencies.git_sources import build_git_source_scope
from src.dependencies.scopes import (
    build_agent_project_tool_scope,
    build_project_agent_scope,
    build_project_knowledge_query_scope,
)
from src.dependencies.services import build_project_agent_config, build_project_tool_executor
from src.exceptions.ai import AgentAnswerError
from src.schemas.context_selection import ContextSelectionInput
from src.schemas.git_sources import GitTaskLinkCreate
from src.services.agent_actions import AgentActionsService
from src.services.git_credentials import GitCredentialCodec
from src.services.git_sources import GitSourceService
from src.services.project_agent import ProjectAgentService
from src.services.project_query import ProjectQueryService
from src.utils.web_grants import WebGrantSigner
from tests.integration.repositories.test_git_sources import Remote, connect, converge
from tests.integration.repositories.test_project_knowledge import map_environment

PATH = "src/service.py"
A, B, C, D, E, F = (character * 40 for character in "abcdef")
REPOSITORIES = {
    "app": (101, E, "APP_IMPLEMENTATION"),
    "api": (102, B, "API_IMPLEMENTATION"),
    "docs": (103, C, "DOCS_UNCHANGED"),
}


class RepoRemote(Remote):
    def __init__(self, name, sha, marker):
        super().__init__()
        self.sha = sha
        self.name = name
        self.ci_sha = D if name == "app" else F
        self.add_version(sha, {PATH: f"def run():\n    return '{marker}'\n".encode()})
        if name == "app":
            self.pr_head, self.pr_merge = D, E
            self.deployments = [
                {
                    "id": 10,
                    "sha": E,
                    "environment": "staging",
                    "created_at": "2026-11-16T11:00:00Z",
                },
                {
                    "id": 11,
                    "sha": E,
                    "environment": "production",
                    "created_at": "2026-11-16T11:01:00Z",
                },
                {
                    "id": 12,
                    "sha": A,
                    "environment": "production",
                    "created_at": "2026-11-16T12:00:00Z",
                },
            ]
            self.statuses = {
                10: [
                    {
                        "id": 100,
                        "state": "success",
                        "environment": "staging",
                        "created_at": "2026-11-16T11:00:01Z",
                    }
                ],
                11: [
                    {
                        "id": 101,
                        "state": "inactive",
                        "environment": "production",
                        "created_at": "2026-11-16T12:00:01Z",
                    }
                ],
                12: [
                    {
                        "id": 102,
                        "state": "success",
                        "environment": "production",
                        "created_at": "2026-11-16T12:00:02Z",
                    }
                ],
            }
        elif name == "api":
            self.pr_head, self.pr_merge = F, None

    def response(self, request):
        if request.url.path.endswith("/check-runs"):
            assert self.db_active == 0
            self.requests.append(request.url.path)
            return httpx.Response(
                200,
                json={
                    "total_count": 1,
                    "check_runs": [
                        {
                            "id": 501 if self.name == "app" else 502,
                            "name": "tests",
                            "head_sha": self.ci_sha,
                            "status": "completed",
                            "conclusion": "success" if self.name == "app" else "failure",
                            "completed_at": "2026-11-16T12:00:00Z",
                        }
                    ],
                },
            )
        return super().response(request)


class MultiRemote(Remote):
    """Независимые upstream identities, даже при одном пути и PR number=7."""

    def __init__(self):
        super().__init__()
        self.repos = {
            name: RepoRemote(name, sha, marker) for name, (_, sha, marker) in REPOSITORIES.items()
        }

    def response(self, request):
        assert self.db_active == 0, "Git HTTP must not hold a DB scope"
        self.requests.append(request.url.path)
        return self.repos[request.url.path.split("/")[3]].response(request)


async def test_three_repositories_two_prs_and_revocation_guard_preserve_exact_context(
    engine, tmp_path
):
    async with map_environment(engine) as env:
        settings = get_settings().model_copy(deep=True)
        settings.app.uploads_path = tmp_path
        settings.knowledge.knowledge_enabled = False
        settings.web_research.web_research_enabled = False
        remote = MultiRemote()
        original_scope = build_git_source_scope(session_factory=env.factory, settings=settings)

        @asynccontextmanager
        async def git_scope():
            remote.db_active += 1
            try:
                async with original_scope() as db:
                    yield db
            finally:
                remote.db_active -= 1

        git = GitSourceService(
            scope=git_scope,
            client_factory=remote.factory,
            credential_codec=GitCredentialCodec(SecretStr(Fernet.generate_key().decode())),
        )
        assert git.limits.max_foreground_requests == 32
        control = SimpleNamespace(
            service=git, sessions=env.factory, project=env.project_id, principal=env.principal
        )
        connections = {name: await connect(control, name) for name in REPOSITORIES}

        # Phase 1: durable worker indexes all three distinct immutable snapshots.
        async def indexed():
            page = await git.list_connections(project_id=env.project_id, principal=env.principal)
            return all(item.indexed_sha == REPOSITORIES[item.repo][1] for item in page.items)

        await converge(control, indexed, ticks=60)
        async with env.factory() as db:
            stage = ProjectStage(
                project_id=env.project_id, name="В работе", color="#334455", is_done_stage=False
            )
            db.add(stage)
            await db.flush()
            task = Task(
                project_id=env.project_id,
                stage_id=stage.id,
                number=1,
                title="Связать форму и API",
                position=1000,
            )
            db.add(task)
            await db.commit()
            task_id, stage_id = task.id, stage.id
        for name in ("app", "api"):
            await git.link_task(
                project_id=env.project_id,
                principal=env.principal,
                task_id=task_id,
                data=GitTaskLinkCreate(
                    connection_id=connections[name].id, kind="pull_request", pull_number=7
                ),
            )

        async def evidence_ready():
            page = await git.get_task_links(
                project_id=env.project_id, principal=env.principal, task_id=task_id
            )
            items = {item.repository_id: item for item in page.items}
            return (
                set(items) == {101, 102}
                and items[101].last_error_code == "checks_remote_read_failed"
                and len(items[101].evidence) == 3
                and not items[102].evidence_partial
            )

        await converge(control, evidence_ready, ticks=60)
        assert max(remote.batches) <= 4  # Per-client/lease, not the foreground turn budget.
        await env.updates.process_project(env.project_id)
        primary = await env.service.read_map(project_id=env.project_id, principal=env.principal)
        assert primary.items
        assert all(
            not source_id.startswith("git_")
            for item in primary.items
            for source_id in item.source_ids
        )

        # Real production assembly; every non-Git external transport is forbidden.
        llm = AsyncMock(spec=LlmClient)
        llm.get_structured_response.side_effect = AssertionError(
            "No paid model requests in this test"
        )
        forbidden = AsyncMock(side_effect=AssertionError("No web/embeddings/Qdrant in this test"))
        runtime = SimpleNamespace(
            embedding_client=SimpleNamespace(get_embedding=forbidden),
            qdrant_client=SimpleNamespace(search=forbidden),
            web_context=SimpleNamespace(read=forbidden, search=forbidden),
            web_grants=WebGrantSigner(secret="synthetic-only"),
        )
        context = build_agent_context_tools(
            session_factory=env.factory, settings=settings, runtime=runtime
        )
        context.git = context.selection.git = context.evidence.git = git
        query = ProjectQueryService(
            scope=build_project_knowledge_query_scope(session_factory=env.factory)
        )
        actions = AgentActionsService(
            scope=build_agent_project_tool_scope(session_factory=env.factory, settings=settings),
            actions=PROJECT_ACTIONS,
            context_tools=context,
        )
        executor = build_project_tool_executor(query, actions)
        actor = replace(env.principal, user_id=env.member_id)

        # Phase 2: shared selection carries exact PR/CI/deployment DTO + private proofs.
        selection = await context.selection.select(
            project_id=env.project_id,
            principal=actor,
            request=ContextSelectionInput(
                question="Проверить состояние формы и API", task_ids=[task_id], limit=5
            ),
        )
        links = selection["completion_evidence"][0]["items"]
        by_repo = {item["repository_id"]: item for item in links}
        assert set(by_repo) == {101, 102}
        assert by_repo[101]["status"] == "merged" and by_repo[101]["commit_sha"] == E
        assert by_repo[102]["status"] == "open" and by_repo[102]["commit_sha"] == F
        app_facts = by_repo[101]["evidence"]
        # The provider offers successful CI for D when asked about merge E. The
        # strict client rejects that identity; deployment facts remain useful.
        assert by_repo[101]["evidence_partial"]
        assert by_repo[101]["last_error_code"] == "checks_remote_read_failed"
        assert not any(item["kind"] == "check" for item in app_facts)
        assert {
            (item["commit_sha"], item["environment"], item["is_current"])
            for item in app_facts
            if item["kind"] == "deployment"
        } == {(E, "staging", True), (E, "production", False), (A, "production", True)}
        assert len(selection.git_proofs) == 2
        async with env.factory() as db:
            assert await db.scalar(select(Task.stage_id).where(Task.id == task_id)) == stage_id
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(GitChange)
                    .where(GitChange.connection_id == connections["docs"].id)
                )
                == 0
            )

        # Phase 3: FunctionModel goes through native tools, common services and
        # the real final publication guard. It deliberately returns no handles.
        async def ask(*, revoke):
            step, read_files = 0, {}
            start = len(remote.requests)

            async def respond(messages, info):
                nonlocal step
                step += 1
                returns = [
                    part
                    for message in messages
                    for part in message.parts
                    if isinstance(part, ToolReturnPart)
                ]
                if returns and returns[-1].tool_name == "telos_read_git_file":
                    value = returns[-1].content
                    assert value["path"] == PATH
                    expected = next(
                        (sha, marker)
                        for _, (repo_id, sha, marker) in REPOSITORIES.items()
                        if repo_id == value["repository_id"]
                    )
                    assert value["commit_sha"] == expected[0] and expected[1] in value["text"]
                    read_files[value["repository_id"]] = (
                        value["commit_sha"],
                        value["path"],
                        value["text"],
                    )
                if step == 1:
                    return ModelResponse(
                        parts=[
                            ToolCallPart("telos_load_capability", {"id": "context"}),
                            ToolCallPart("telos_load_capability", {"id": "git"}),
                        ]
                    )
                if step == 2:
                    return ModelResponse(
                        parts=[
                            ToolCallPart(
                                "telos_select_context",
                                {
                                    "question": "Состояние формы и API",
                                    "task_ids": [task_id],
                                    "limit": 5,
                                },
                            )
                        ]
                    )
                if 3 <= step <= 5:
                    name = ("app", "api", "docs")[step - 3]
                    return ModelResponse(
                        parts=[
                            ToolCallPart(
                                "telos_read_git_file",
                                {
                                    "connection_id": connections[name].id,
                                    "sha": REPOSITORIES[name][1],
                                    "path": PATH,
                                    "max_chars": 1000,
                                },
                            )
                        ]
                    )
                assert set(read_files) == {101, 102, 103}
                if revoke:
                    remote.repos["api"].denied.add("api")
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "telos_submit_answer",
                            {
                                "answer": "Три самостоятельных источника: APP_IMPLEMENTATION, API_IMPLEMENTATION, DOCS_UNCHANGED. Изменения задачи связаны только с app и api; API PR ещё открыт. Успех CI другого SHA не подтверждает merge, staging не production, production откатан на прежний SHA.",
                                "source_ids": [],
                            },
                        )
                    ]
                )

            service = ProjectAgentService(
                scope=build_project_agent_scope(session_factory=env.factory, settings=settings),
                llm_client=llm,
                embedding_client=runtime.embedding_client,
                qdrant_client=runtime.qdrant_client,
                config=build_project_agent_config(settings),
                tools=executor,
                agent_model=FunctionModel(respond),
                context_tools=context,
            )
            execution = AgentToolContext(
                project_id=env.project_id,
                user_id=actor.user_id,
                can_write=False,
                principal=actor,
                request_id=uuid4(),
            )
            value = await service.ask(
                project_id=env.project_id,
                question="Сравни три репозитория и состояние связанной задачи без изменений.",
                history=[],
                execution=execution,
            )
            assert len(remote.requests) - start <= 32
            assert value._context_evidence[0].git_budget.requests_made <= 32
            return value

        published = await ask(revoke=False)
        assert all(marker in published.answer for _, _, marker in REPOSITORIES.values())
        files = [item for item in published.external_sources if item.kind == "git_file"]
        assert {(item.repository_id, item.commit_sha, item.path) for item in files} == {
            (repository_id, sha, PATH) for repository_id, sha, _ in REPOSITORIES.values()
        }
        assert "API PR ещё открыт" in published.answer
        assert "production откатан" in published.answer
        before_revoked_turn = len(remote.requests)
        with pytest.raises(AgentAnswerError):
            await ask(revoke=True)
        # Revoke happens only after all three native readers returned. An
        # unrelated earlier tool failure cannot satisfy this negative phase.
        assert remote.repos["api"].denied == {"api"}
        assert len(remote.requests) - before_revoked_turn <= 32
        llm.get_structured_response.assert_not_awaited()
        forbidden.assert_not_awaited()
