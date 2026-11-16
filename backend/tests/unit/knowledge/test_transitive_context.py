"""R2/R4: inherited source dependencies and actual Git attempt budget across queue phases."""

from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic_ai.models.function import FunctionModel

from src.agent.aris import ArisMode
from src.agent.tools import AgentToolContext
from src.clients.github import GitHubClient
from src.exceptions.access import ResourceNotAvailableError
from src.exceptions.ai import AgentAnswerError
from src.exceptions.context_selection import ContextSelectionChangedError
from src.knowledge.catalog import build_catalog
from src.schemas.git_sources import GitSourceProof
from src.schemas.knowledge import KnowledgeAnswerSchema, encode_answer_sources
from src.schemas.project_tool_inputs import ChatMessageInput, ChatMessagesInput
from src.services.agent_chat_files import ChatFileContext, ChatFileLocation
from src.services.agent_chat_history import AgentChatHistoryService
from src.services.agent_chat_history_actions import get_chat_message, list_chat_messages
from src.services.agent_conversations import AgentConversationsService
from src.services.aris_chat import ArisChatService
from src.services.context_evidence import ContextEvidence
from src.services.context_results import ContextReadResult, HistoryReadResult
from src.services.git_credentials import GitCredentialCodec
from src.services.git_sources import GitSourceService
from tests.unit.knowledge.agent_models import final
from tests.unit.knowledge.test_chat_history_context import message
from tests.unit.knowledge.test_project_agent import build_service
from tests.unit.services.test_context_evidence import evidence_environment


def budget_service():
    return GitSourceService(
        scope=AsyncMock(), client_factory=AsyncMock(), credential_codec=GitCredentialCodec(None)
    )


@pytest.mark.parametrize("reader", ["initial", "list", "get"])
async def test_actual_history_readers_keep_private_transitive_guard_outside_model_json(reader):
    state, verifier, principal, _, _, _ = evidence_environment()
    verifier.git = budget_service()
    previous = ContextEvidence()
    previous.internal(1, build_catalog(1, state.rows).sources["project:1"])
    stored_answer = KnowledgeAnswerSchema(answer="PRIVATE_HISTORY_CONTROL", sources=[])
    stored_answer._context_evidence = previous, principal
    source_rows = []
    for identifier, author, text in ((11, "AGENT", stored_answer.answer), (12, "USER", "Продолжи")):
        row = message(identifier, text, kind="conversation")
        row.update(
            author_type=author,
            ordinal=identifier,
            entity_sources=encode_answer_sources(stored_answer) if author == "AGENT" else [],
        )
        row["reply_to_message_id"] = None
        source_rows.append(row)
    repository = SimpleNamespace(
        get_message=AsyncMock(
            side_effect=lambda **kwargs: deepcopy(
                next(row for row in source_rows if row["id"] == kwargs["message_id"])
            )
        ),
        list_messages=AsyncMock(side_effect=lambda **_: deepcopy(source_rows[:1])),
        attachments=AsyncMock(return_value=[]),
    )

    @asynccontextmanager
    async def history_scope():
        yield SimpleNamespace(history=repository)

    files = SimpleNamespace(
        location=AsyncMock(
            return_value=ChatFileLocation(
                kind="conversation", scope_id=1, question_id=12, upper_bound=12
            )
        )
    )
    history = AgentChatHistoryService(files=files, scope=history_scope, evidence=verifier)
    context = AgentToolContext(user_id=1, project_id=None, message_id=12, principal=principal)
    if reader == "initial":
        result = await history.initial_context(
            context=ChatFileContext(user_id=1, project_id=None, message_id=12, principal=principal)
        )
    elif reader == "list":
        result = await list_chat_messages(history, context, ChatMessagesInput())
    else:
        result = await get_chat_message(history, context, ChatMessageInput(message_id=11))
    assert "PRIVATE_HISTORY_CONTROL" in str(dict(result))
    assert "context_guard" not in str(dict(result)) and "source_hash" not in str(dict(result))
    current = ContextEvidence()
    current.register(result)
    assert current.saved.internal == previous.saved.internal
    state.rows["projects"][0]["description_md"] = "Изменённый исходный паспорт"
    with pytest.raises(ContextSelectionChangedError):
        await verifier.validate(current.saved, principal=principal)


@pytest.mark.parametrize("revoke_during_model", [False, True])
async def test_history_paraphrase_without_handles_keeps_old_git_proof_until_final_and_persisted_read(
    revoke_during_model,
):
    _, verifier, principal, _, git, _ = evidence_environment()
    proof = GitSourceProof(
        source_id="git_file:6",
        entity_id=6,
        file_id=6,
        project_id=1,
        connection_id=2,
        repository_id=101,
        ref="main",
        commit_sha="a" * 40,
        path="private.py",
        blob_sha="b" * 40,
        text_hash="c" * 64,
        generation=3,
        credential_revision=2,
    )
    old = ContextEvidence()
    old.register(ContextReadResult({}, project_id=1, git_proofs=(proof,)))
    await verifier.validate(old.saved, principal=principal)
    public_old = message(
        11, "Private code statement TRANSITIVE_PRIVATE_CONTROL", kind="conversation"
    )
    public_old["author_type"] = "AGENT"
    history = HistoryReadResult(
        {
            "kind": "conversation",
            "chat_id": 1,
            "timezone": "UTC",
            "current_message": message(12, "Перескажи сказанное выше", kind="conversation"),
            "messages": [public_old],
            "quoted_message": None,
        },
        guards=(old.saved,),
    )
    service, *_ = build_service()
    service.context_tools = SimpleNamespace(
        git=budget_service(), evidence=verifier, web=SimpleNamespace()
    )

    async def respond(_messages, _info):
        if revoke_during_model:
            git.validate_sources.side_effect = ResourceNotAvailableError(
                resource="private repository", resource_id=101
            )
        return final("Пересказ TRANSITIVE_PRIVATE_CONTROL", source_ids=[])

    service.agent_model = FunctionModel(respond)
    request = service.ask_personal(
        question="Перескажи сказанное выше",
        history=[],
        memory="",
        actor={"user_id": 1},
        execution=AgentToolContext(
            project_id=None, user_id=1, principal=principal, mode=ArisMode.WIDGET_PERSONAL
        ),
        chat_context=history,
    )
    if revoke_during_model:
        with pytest.raises(AgentAnswerError):
            await request
    else:
        answer = await request
        assert answer.sources == [] and answer.message_references == []
        assert answer._context_evidence[0].saved.git_files == [proof]
        stored = encode_answer_sources(answer)
        git.validate_sources.side_effect = ResourceNotAvailableError(
            resource="private repository", resource_id=101
        )
        public, available = await verifier.present(stored, user_id=1)
        assert not available and not any(public.values())
    assert git.validate_sources.await_count >= 2


@pytest.mark.parametrize("channel", ["widget", "shared"])
async def test_queue_history_generation_and_finish_share_one_actual_32_attempt_ledger(channel):
    git = budget_service()
    agent = SimpleNamespace(context_tools=SimpleNamespace(git=git))
    row = SimpleNamespace(id=1, conversation_id=1, project_id=1)
    db = SimpleNamespace(
        unit_of_work=SimpleNamespace(commit=AsyncMock()),
        messages=SimpleNamespace(fail_expired=AsyncMock(), claim_next=AsyncMock(return_value=row)),
        turns=SimpleNamespace(
            claim_expired=AsyncMock(return_value=None), claim_next=AsyncMock(return_value=row)
        ),
    )

    @asynccontextmanager
    async def scope():
        yield db

    config = SimpleNamespace(turn_timeout_seconds=30)
    queue = (
        AgentConversationsService(scope=scope, agent=agent, llm_client=AsyncMock(), config=config)
        if channel == "widget"
        else ArisChatService(scope=scope, agent=agent, config=config)
    )
    transport_calls, ledgers, published, failed = [], [], [], []

    async def transport(request):
        transport_calls.append(request.url)
        return httpx.Response(
            200,
            json={
                "id": 101,
                "owner": {"login": "demo"},
                "name": "repo",
                "default_branch": "main",
                "private": False,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:

        async def phase(count):
            async with git.read_budget() as budget:
                ledgers.append(budget)
                client = GitHubClient(http_client=http, request_budget=budget)
                for _ in range(count):
                    await client.get_repository("demo", "repo")

        async def generate(_row):
            await phase(17)  # authenticated history source checks
            await phase(14)  # model-requested context reads
            answer = KnowledgeAnswerSchema(
                answer="Не публиковать без final source checks", sources=[]
            )
            return (answer, 1) if channel == "widget" else answer

        async def finish(_row, *, answer=None, error=None, **_kwargs):
            if error is not None:
                failed.append(error)
                return
            await phase(2)  # final guard has only one remaining attempt
            published.append(answer)

        queue._generate, queue._finish = generate, finish
        assert await queue.process_next()
    assert len(transport_calls) == 32 and not published and failed
    assert len({id(ledger) for ledger in ledgers}) == 1
    assert ledgers[0].remaining == 0 and ledgers[0].exhausted
