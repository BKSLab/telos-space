"""Настоящий native runtime с FunctionModel, без сетевого/платного LLM."""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from src.agent.aris import ArisMode
from src.agent.runtime import ArisRuntime, WriteGate, _fit_result
from src.agent.tools import AgentTool, AgentToolExecutor, AgentToolRequest
from src.db.models.api_tokens import ApiTokenScope
from src.dependencies.services import ARIS_ACTIONS, build_project_tool_executor
from src.prompts.project_agent import ARIS_BASE_PROMPTS
from src.schemas.context_selection import KnowledgeMapReadInput
from src.schemas.project_knowledge import (
    ProjectKnowledgeClaimSchema,
    ProjectKnowledgeMapSchema,
    ProjectKnowledgeObservationSchema,
)
from src.schemas.web_research import WebReadSchema
from src.services.agent_actions import AgentActionsService
from src.services.auth import ApiKeyGrant
from src.services.context_evidence import ContextEvidenceService
from src.services.context_selection import ContextSelectionService, map_summary
from tests.unit.knowledge.agent_models import final
from tests.unit.knowledge.test_aris_runtime import deps_for


async def test_agent_ask_scope_alone_never_opens_or_executes_context_read_tools():
    handler = AsyncMock(return_value={"private": "never returned"})
    tool = AgentTool(
        "telos_get_knowledge_map",
        "Карта",
        KnowledgeMapReadInput,
        handler,
        required_scopes=frozenset({"knowledge:read", "projects:read"}),
    )
    executor = AgentToolExecutor([tool])
    deps = deps_for(executor, can_write=False)
    principal = replace(
        ContextEvidenceService.session_principal(1),
        scope=ApiTokenScope.READ,
        api_key=ApiKeyGrant(
            key_id=uuid4(),
            label="ordinary chat",
            key_prefix="test",
            owner_user_id=1,
            scopes=frozenset({"agent:ask"}),
        ),
    )
    deps.execution = replace(deps.execution, principal=principal)
    assert not deps.allowed(tool.name)
    denied = await executor.execute(deps.execution, AgentToolRequest(name=tool.name, arguments={}))
    assert "error" in denied and "private" not in denied
    handler.assert_not_awaited()
    for scopes in ({"knowledge:read"}, {"projects:read"}):
        deps.execution = replace(
            deps.execution,
            principal=replace(
                principal, api_key=replace(principal.api_key, scopes=frozenset(scopes))
            ),
        )
        assert not deps.allowed(tool.name)
    deps.execution = replace(
        deps.execution,
        principal=replace(
            principal,
            api_key=replace(
                principal.api_key, scopes=frozenset({"knowledge:read", "projects:read"})
            ),
        ),
    )
    assert deps.allowed(tool.name)
    assert await executor.execute(
        deps.execution, AgentToolRequest(name=tool.name, arguments={})
    ) == {"private": "never returned"}


async def test_native_web_budget_limits_handlers_and_preserves_signed_identity_under_compaction():
    token = "signed." + "a" * 1300
    handler = AsyncMock(
        return_value={
            "page_token": token,
            "source_id": "web:" + "b" * 64,
            "text": "длинный текст " * 4000,
            "offset": 0,
            "next_offset": None,
        }
    )
    tool = AgentTool("telos_read_web_page", "Читать", WebReadSchema, handler)
    executor = AgentToolExecutor([tool])
    deps = deps_for(executor, can_write=False, tool_rounds=7)
    step = 0

    async def respond(messages, _info):
        nonlocal step
        step += 1
        for message in messages:
            for part in message.parts:
                if (
                    isinstance(part, ToolReturnPart)
                    and isinstance(part.content, dict)
                    and "page_token" in part.content
                ):
                    assert part.content["page_token"] == token
        if step <= 5:
            return ModelResponse(
                parts=[ToolCallPart(tool.name, {"page_token": token, "max_chars": 8000})]
            )
        return final("Предел достигнут; это частичное чтение.")

    result = await ArisRuntime(model=FunctionModel(respond), executor=executor).run(
        deps, metadata={}
    )
    assert handler.await_count == 4
    assert all(call.args[1].page_token == token for call in handler.await_args_list)
    assert result.tool_calls[-1]["refused"] == "web_limit"
    assert result.output.answer.startswith("Предел достигнут")
    too_large = _fit_result({"page_token": "unshortenable." + "x" * 20000}, 100)
    assert too_large["reason"] == "context_budget" and "page_token" not in too_large


async def test_two_loaded_capabilities_select_page_six_and_next_task_fit_real_context_budget():
    """Regression live34 shape: 2 protected loader results + select(limit5), 12k budget."""
    observation = ProjectKnowledgeObservationSchema(project_id=1, generation=7, sources=[])
    claims = [
        ProjectKnowledgeClaimSchema(
            id=uuid4(),
            revision=1,
            kind="requirement",
            category="goal",
            text=("Проверяемое требование владельца с существенными ограничениями. " * 20)[:1600],
            state="current",
            acceptance="accepted",
            verification="owner_statement",
            source_ids=["project:1"],
            updated_at=datetime.now(UTC),
            provenance={
                "origin": "project_passport",
                "explanation": "Это заявление, а не проверенный production. " * 12,
            },
        )
        for _ in range(6)
    ]
    claims[-1] = claims[-1].model_copy(
        update={
            "text": "REPORT_V2: участник сообщил результат, независимой проверки нет.",
            "kind": "source_fact",
            "category": "task_results",
            "verification": "participant_report",
            "source_ids": ["task_report:2"],
        }
    )
    first = ProjectKnowledgeMapSchema(
        project_id=1,
        generation=7,
        built_generation=7,
        status="ready",
        observation=observation,
        items=claims[:5],
        total=6,
        offset=0,
        limit=5,
        has_more=True,
        next_offset=5,
    )
    last = first.model_copy(
        update={"items": claims[5:], "offset": 5, "has_more": False, "next_offset": None}
    )
    knowledge = SimpleNamespace(
        capture_observation=AsyncMock(return_value=observation),
        read_map=AsyncMock(return_value=first),
    )
    selector = ContextSelectionService(
        knowledge=knowledge,
        query=SimpleNamespace(
            get_project_overview=AsyncMock(
                return_value=SimpleNamespace(
                    summary=SimpleNamespace(project_key="P", name="Synthetic"),
                    description_sections={},
                    description="",
                    context={
                        "revision": 1,
                        "fields": {},
                        "unknown_fields": ["goal", "boundaries", "constraints"],
                        "truncated_fields": [],
                    },
                )
            )
        ),
        search=SimpleNamespace(search=AsyncMock(return_value=[])),
        git=SimpleNamespace(get_task_links=AsyncMock()),
    )

    @asynccontextmanager
    async def unused_scope():
        raise AssertionError("Budget test must use declared synthetic handlers")
        yield

    actions = AgentActionsService(scope=unused_scope, actions=ARIS_ACTIONS)
    submitted = []

    async def dispatch(*, context, name, arguments):
        if name == "telos_select_context":
            from src.schemas.context_selection import ContextSelectionInput

            return await selector.select(
                project_id=1,
                principal=context.principal,
                request=ContextSelectionInput(**arguments),
            )
        if name == "telos_get_knowledge_map":
            assert arguments["offset"] == 5
            return map_summary(last)
        if name == "telos_get_knowledge_claim":
            assert arguments["claim_id"] == str(claims[-1].id)
            return claims[-1].model_dump(mode="json")
        assert name == "telos_create_task"
        submitted.append(arguments)
        return {
            "action": {
                "id": str(uuid4()),
                "tool_name": name,
                "title": "Создать задачу",
                "status": "completed",
                "result": {"source_id": "task:8", "task": {"id": 8, "title": arguments["title"]}},
            }
        }

    actions.execute = AsyncMock(side_effect=dispatch)
    executor = build_project_tool_executor(SimpleNamespace(), actions)
    deps = deps_for(executor, can_write=True, tool_rounds=8)
    deps.execution = replace(deps.execution, principal=ContextEvidenceService.session_principal(1))
    deps.system_prompt = ARIS_BASE_PROMPTS[ArisMode.WIDGET_PROJECT]
    deps.budget.system_prompt = deps.system_prompt
    deps.content.update(
        question="Прочитай доступные результаты и создай следующий проверяемый шаг без предположений о production.",
        current_postgres_state={
            "project": {
                "key": "P",
                "context": {
                    "revision": 1,
                    "fields": {},
                    "unknown_fields": ["goal", "boundaries", "constraints"],
                },
            }
        },
    )
    deps.defer_workflows = True

    async def allowed_write():
        return True

    deps.write_gate_factory = lambda: WriteGate(allowed_write())
    step = 0

    async def respond(messages, _info):
        nonlocal step
        step += 1
        calls = [
            ("telos_load_capability", {"id": "context"}),
            ("telos_load_capability", {"id": "tasks"}),
            ("telos_select_context", {"question": "Результаты и следующий шаг", "limit": 5}),
            ("telos_get_knowledge_map", {"offset": 5, "limit": 5}),
            ("telos_get_knowledge_claim", {"claim_id": str(claims[-1].id)}),
            (
                "telos_create_task",
                {
                    "title": "Проверить заявленный результат",
                    "description_md": "REPORT_V2 — сообщение участника. Проверить фактическое поведение по указанным ограничениям.",
                },
            ),
        ]
        if step <= len(calls):
            return ModelResponse(parts=[ToolCallPart(*calls[step - 1])])
        return final("Создан следующий шаг проверки, production не подтверждён.")

    try:
        result = await ArisRuntime(model=FunctionModel(respond), executor=executor).run(
            deps, metadata={}
        )
    finally:
        if deps.write_gate:
            deps.write_gate.close()
    assert result.output is not None and result.exhaustion_reason is None
    assert (
        "REPORT_V2" in submitted[0]["description_md"]
        and "assignee" not in submitted[0]
        and "due_date" not in submitted[0]
    )
    assert [call["tool"] for call in result.tool_calls][-3:] == [
        "telos_get_knowledge_map",
        "telos_get_knowledge_claim",
        "telos_create_task",
    ]
