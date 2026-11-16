"""Native ARIS + общие действия + настоящий PostgreSQL, без paid LLM/интернета."""

# ruff: noqa: F811 -- pytest внедряет импортированные фикстуры по имени параметра.

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import delete, select

from src.agent.context import AgentContextBudget
from src.agent.runtime import ArisDeps, ArisRuntime, WriteGate
from src.core.settings import get_settings
from src.db.models import AgentConversation, AgentMessage, AgentToolRun, ProjectMember, Task
from src.db.models.api_tokens import ApiTokenScope
from src.db.models.documents import Document
from src.dependencies.agent_context import build_agent_context_tools
from src.dependencies.scopes import build_project_knowledge_query_scope
from src.dependencies.services import build_project_tool_executor
from src.exceptions.agent_tools import (
    AgentToolAccessError,
    AgentToolRunsRepositoryError,
    AgentToolsServiceError,
)
from src.repositories.agent_tool_runs import AgentToolRunsRepository
from src.schemas.agent_context import WebTopicInput
from src.schemas.knowledge import KnowledgeAnswerSchema, encode_answer_sources
from src.schemas.web_context import WebReadResult
from src.services.context_evidence import (
    HIDDEN_CONTEXT_ANSWER,
    ContextEvidence,
    ContextEvidenceService,
)
from src.services.project_query import ProjectQueryService
from src.utils.web_grants import WebGrantSigner
from tests.integration.repositories.test_agent_conversations_repository import (
    agent_env as agent_env,
)
from tests.integration.repositories.test_agent_project_actions import actions_env as actions_env
from tests.integration.repositories.test_agent_project_actions import invoke
from tests.integration.repositories.test_agent_project_actions import task as create_task


@pytest.mark.parametrize("kind", ["draft", "clarify", "decompose", "create"])
async def test_native_formulation_modes_keep_discussion_read_only_and_explicit_create_persists_real_card(
    actions_env, kind
):
    env = actions_env
    await invoke(env, "telos_create_stage", {"name": "Бэклог", "color": "#334455"})
    proposal = {
        "title": "Сохранить введённый текст",
        "expected_result": "Повторное открытие показывает сохранённый текст",
        "checklist": ["Открыть форму заново"],
    }
    arguments = {
        "draft": {"kind": "draft", "draft": proposal},
        "clarify": {
            "kind": "clarify",
            "question": "Какое поведение при повторном открытии ожидается?",
        },
        "decompose": {
            "kind": "decompose",
            "parts": [{"title": "Исследовать потерю текста"}, proposal],
        },
        "create": {
            "title": proposal["title"],
            "description_md": "## Ожидаемый результат\n" + proposal["expected_result"],
            "checklist": {
                "title": "Проверка",
                "items": [{"text": "Открыть форму заново", "is_completed": False}],
            },
        },
    }[kind]
    executor = build_project_tool_executor(
        ProjectQueryService(scope=build_project_knowledge_query_scope(session_factory=env.factory)),
        env.actions,
    )
    calls, step = [], 0

    async def write_intent():
        return kind == "create"

    async def respond(_messages, _info):
        nonlocal step
        step += 1
        if step == 1:
            return ModelResponse(parts=[ToolCallPart("telos_load_capability", {"id": "tasks"})])
        if step == 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "telos_create_task" if kind == "create" else "telos_prepare_task", arguments
                    )
                ]
            )
        return ModelResponse(
            parts=[ToolCallPart("telos_submit_answer", {"answer": "Подготовлено."})]
        )

    async def observe(_tool, value):
        calls.append(value)

    deps = ArisDeps(
        execution=replace(env.context, request_id=uuid4()),
        executor=executor,
        system_prompt="Проверь поручение и подготовь результат.",
        content={"question": "Создай задачу" if kind == "create" else "Помоги сформулировать"},
        budget=AgentContextBudget(
            system_prompt="Проверь поручение", max_tokens=12000, history_tokens=1000
        ),
        tool_rounds=4,
        defer_workflows=True,
        write_gate_factory=lambda: WriteGate(write_intent()),
        on_result=observe,
    )
    try:
        result = await ArisRuntime(model=FunctionModel(respond), executor=executor).run(
            deps, metadata={}
        )
        assert result.output.answer == "Подготовлено."
    finally:
        if deps.write_gate:
            deps.write_gate.close()
    async with env.factory() as session:
        tasks = (await session.scalars(select(Task).where(Task.project_id == 1))).all()
        receipts = (
            await session.scalars(
                select(AgentToolRun).where(AgentToolRun.tool_name == "telos_create_task")
            )
        ).all()
    if kind == "create":
        assert (
            tasks[0].title == proposal["title"]
            and proposal["expected_result"] in tasks[0].description_md
        )
        assert tasks[0].assignee is None and tasks[0].due_date is None
        assert tasks[0].checklist["items"][0]["text"] == "Открыть форму заново"
        assert (
            calls[0]["action"]["result"]["task"]["id"] == tasks[0].id
            and receipts[0].status == "completed"
        )
    else:
        assert not tasks and not receipts
        assert calls[0]["kind"] == kind and "action" not in calls[0]
        if kind != "clarify":
            assert calls[0]["requires_explicit_creation"] is True
            assert all(
                "assignee" not in item and "due_date" not in item for item in calls[0]["proposals"]
            )


def context_resources(env):
    settings = get_settings().model_copy(deep=True)
    settings.app.uploads_path = env.files
    settings.knowledge.knowledge_enabled = False
    settings.web_research.web_research_enabled = True
    client = SimpleNamespace(read=AsyncMock(), search=AsyncMock())
    runtime = SimpleNamespace(
        embedding_client=AsyncMock(),
        qdrant_client=AsyncMock(),
        web_context=client,
        web_grants=WebGrantSigner(secret="test-consent-only"),
    )
    tools = build_agent_context_tools(
        session_factory=env.factory, settings=settings, runtime=runtime
    )
    env.actions.context_tools = tools
    return tools, client, runtime.web_grants


async def test_read_initiator_approves_exact_durable_external_payload_and_write_stays_forbidden(
    actions_env,
):
    env = actions_env
    tools, client, signer = context_resources(env)
    actor = replace(
        env.context,
        user_id=2,
        can_write=False,
        principal=ContextEvidenceService.session_principal(2),
    )
    request_id = uuid4()
    response = await invoke(
        env,
        "telos_request_web_search",
        {"public_topic": "Метод критического пути"},
        request_id=request_id,
        context=actor,
    )
    action = response["action"]
    assert action["status"] == "pending" and action["effect"] == "external_release"
    assert action["arguments"] == {"public_topic": "Метод критического пути"}
    assert "topic_token" not in str(action)
    with pytest.raises(AgentToolAccessError):
        await tools.execute(
            "telos_request_web_search", actor, WebTopicInput(public_topic="Самоподтверждение")
        )
    with pytest.raises(AgentToolAccessError):
        await env.actions.decide(
            context=env.context, action_id=UUID(action["id"]), decision="approve"
        )
    approved = await env.actions.decide(
        context=actor, action_id=UUID(action["id"]), decision="approve"
    )
    payload = signer.verify(approved.result["topic_token"], project_id=1, user_id=2, kind="topic")
    assert payload["q"] == "Метод критического пути" and payload["a"] == action["id"]
    repeated = await invoke(
        env,
        "telos_request_web_search",
        {"public_topic": "Метод критического пути"},
        request_id=request_id,
        context=actor,
    )
    assert repeated["action"]["id"] == action["id"] and repeated["action"]["status"] == "completed"
    client.read.assert_not_awaited()
    client.search.assert_not_awaited()
    with pytest.raises(AgentToolAccessError):
        await invoke(env, "telos_create_task", {"title": "Не создавать"}, context=actor)


async def test_external_consent_is_revalidated_after_membership_revocation(actions_env):
    env = actions_env
    _, client, _ = context_resources(env)
    actor = replace(
        env.context,
        user_id=2,
        can_write=False,
        principal=ContextEvidenceService.session_principal(2),
    )
    response = await invoke(
        env, "telos_request_web_page", {"url": "https://example.org/guide"}, context=actor
    )
    async with env.factory() as session:
        await session.execute(
            delete(ProjectMember).where(ProjectMember.project_id == 1, ProjectMember.user_id == 2)
        )
        await session.commit()
    with pytest.raises(AgentToolsServiceError):
        await env.actions.decide(
            context=actor, action_id=UUID(response["action"]["id"]), decision="approve"
        )
    async with env.factory() as session:
        row = await session.get(AgentToolRun, UUID(response["action"]["id"]))
        assert row.status == "pending" and "page_token" not in str(row.result)
    client.read.assert_not_awaited()


async def test_read_credential_cannot_approve_pending_business_write(actions_env):
    env = actions_env
    item = await create_task(env)
    response = await invoke(
        env, "telos_delete_task", {"task_id": item["id"], "expected_updated_at": item["updated_at"]}
    )
    pending = response["action"]
    actor = replace(
        env.context, can_write=False, principal=ContextEvidenceService.session_principal(1)
    )
    with pytest.raises(AgentToolAccessError):
        await env.actions.decide(context=actor, action_id=UUID(pending["id"]), decision="approve")
    async with env.factory() as session:
        assert (await session.get(AgentToolRun, UUID(pending["id"]))).status == "pending"
        assert (await session.get(Task, item["id"])).title == item["title"]


async def test_web_research_action_replays_without_network_and_joins_document_receipt_transaction(
    actions_env,
):
    env = actions_env
    tools, client, _ = context_resources(env)
    principal = replace(ContextEvidenceService.session_principal(1), scope=ApiTokenScope.WRITE)
    actor = replace(env.context, principal=principal)
    grant = await tools.web.approve_url(
        project_id=1, principal=principal, url="https://example.org/guide", approval_id="human"
    )
    client.read.return_value = WebReadResult(
        source_id="web:" + "a" * 64,
        url="https://example.org/guide",
        requested_url="https://example.org/guide",
        title="Guide",
        text="Verified source",
        fetched_at=datetime.now(UTC),
        content_hash="b" * 64,
        coverage="extracted_text",
        total_chars=15,
        media_type="text/plain",
        downloaded_bytes=15,
    )
    args = {"page_token": grant.page_token, "expected_hash": "b" * 64, "title": "Исследование"}
    request_id = uuid4()
    first = await invoke(env, "telos_save_web_research", args, context=actor, request_id=request_id)
    client.read.side_effect = AssertionError("Завершённый повтор не должен вызывать сеть")
    second = await invoke(
        env, "telos_save_web_research", args, context=actor, request_id=request_id
    )
    assert first["action"]["id"] == second["action"]["id"]
    assert (
        first["action"]["result"]["document"]["ref"]
        == second["action"]["result"]["document"]["ref"]
    )
    assert client.read.await_count == 1


async def test_receipt_failure_rolls_back_web_document_and_same_request_can_be_retried(
    actions_env, monkeypatch
):
    env = actions_env
    tools, client, _ = context_resources(env)
    principal = replace(ContextEvidenceService.session_principal(1), scope=ApiTokenScope.WRITE)
    actor = replace(env.context, principal=principal)
    grant = await tools.web.approve_url(
        project_id=1, principal=principal, url="https://example.org/guide", approval_id="human"
    )
    client.read.return_value = WebReadResult(
        source_id="web:" + "a" * 64,
        url="https://example.org/guide",
        requested_url="https://example.org/guide",
        title="Guide",
        text="Verified source",
        fetched_at=datetime.now(UTC),
        content_hash="b" * 64,
        coverage="extracted_text",
        total_chars=15,
        media_type="text/plain",
        downloaded_bytes=15,
    )
    args = {
        "page_token": grant.page_token,
        "expected_hash": "b" * 64,
        "title": "Исследование с откатом",
    }
    request_id = uuid4()
    original_save = AgentToolRunsRepository.save

    async def fail_receipt(_repository, _data):
        raise AgentToolRunsRepositoryError("injected receipt failure")

    monkeypatch.setattr(AgentToolRunsRepository, "save", fail_receipt)
    with pytest.raises(AgentToolsServiceError):
        await invoke(env, "telos_save_web_research", args, context=actor, request_id=request_id)
    async with env.factory() as db:
        assert not (await db.scalars(select(Document).where(Document.title == args["title"]))).all()
        assert not (
            await db.scalars(select(AgentToolRun).where(AgentToolRun.request_id == request_id))
        ).all()
    monkeypatch.setattr(AgentToolRunsRepository, "save", original_save)
    completed = await invoke(
        env, "telos_save_web_research", args, context=actor, request_id=request_id
    )
    assert completed["action"]["status"] == "completed"


async def test_private_global_history_rechecks_all_read_documents_after_revoke_without_model_handles(
    actions_env,
):
    env = actions_env
    tools, client, _ = context_resources(env)
    env.responder.context_tools = tools
    principal = replace(ContextEvidenceService.session_principal(1), scope=ApiTokenScope.WRITE)
    text = "Private project text DOCUMENT_WITHOUT_HANDLE_CONTROL"
    created = await invoke(
        env,
        "telos_create_document",
        {"title": "Исходный документ", "content_md": text},
        context=replace(env.context, principal=principal),
    )
    document = await tools.web.documents.get(
        document_ref=created["action"]["result"]["document"]["ref"], user_id=1, principal=principal
    )
    ledger = ContextEvidence()
    ledger.document(document)
    answer = KnowledgeAnswerSchema(answer=text, sources=[])
    answer._context_evidence = ledger, principal
    async with env.factory() as db:
        conversation = AgentConversation(
            user_id=1, title="Личный разговор", project_id=None, channel="web"
        )
        db.add(conversation)
        await db.flush()
        saved = AgentMessage(
            conversation_id=conversation.id,
            request_id=uuid4(),
            role="assistant",
            content=text,
            sources=encode_answer_sources(answer),
            status="completed",
        )
        db.add(saved)
        await db.commit()
        conversation_id, message_id = conversation.id, saved.id
    page = await env.service.get_messages(
        user_id=1, conversation_id=conversation_id, before_id=None, limit=20
    )
    assert page.items[0].content == text and page.items[0].sources == []
    assert "context_guard" not in str(page.model_dump())
    async with env.factory() as db:
        await db.execute(
            delete(ProjectMember).where(ProjectMember.project_id == 1, ProjectMember.user_id == 1)
        )
        await db.commit()
    denied = await env.service.get_messages(
        user_id=1, conversation_id=conversation_id, before_id=None, limit=20
    )
    assert denied.items[0].content == HIDDEN_CONTEXT_ANSWER and not denied.items[0].actions
    async with env.factory() as db:
        assert (await db.get(AgentMessage, message_id)).content == text
    client.read.assert_not_awaited()
