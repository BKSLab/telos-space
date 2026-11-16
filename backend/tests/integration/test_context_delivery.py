"""PostgreSQL delivery guard: persisted sources, broadcast, current reader и stale body."""

from uuid import uuid4

from sqlalchemy import select, update

from src.core.settings import get_settings
from src.db.models import ChatAgentTurn, ChatMessage, Project
from src.dependencies.agent_context import build_context_evidence_service
from src.knowledge.catalog import build_catalog
from src.schemas.knowledge import (
    ExternalSourceReferenceSchema,
    KnowledgeAnswerSchema,
    encode_answer_sources,
)
from src.schemas.project_chats import ChatMessageCreate
from src.services.context_evidence import HIDDEN_CONTEXT_ANSWER, ContextEvidence


async def test_shared_persisted_reply_is_hidden_in_broadcast_and_revalidated_on_history_get(
    chat_env,
):
    env = chat_env
    guard = build_context_evidence_service(session_factory=env.factory, settings=get_settings())
    env.service.presenter.evidence = guard
    original = "Сохранённая исходная цель с контрольным кодом SOURCE_PRIVATE_173"
    async with env.factory() as db:
        await db.execute(update(Project).where(Project.id == 1).values(description_md=original))
        await db.commit()
    async with guard.scope() as db:
        catalog = build_catalog(1, await db.sources.get_project_rows(1))
    ledger = ContextEvidence()
    ledger.internal(1, catalog.sources["project:1"])
    answer = KnowledgeAnswerSchema(
        answer=original,
        sources=[],
        external_sources=[
            ExternalSourceReferenceSchema(
                source_id="web:" + "a" * 64,
                kind="web_page",
                project_id=1,
                title="Public observation",
                url="https://example.org/guide",
                coverage="extracted_text",
                content_hash="b" * 64,
            )
        ],
    )
    answer._context_evidence = ledger, guard.session_principal(1)
    await env.service.send(
        1, 1, ChatMessageCreate(content="Арис, проверь текущую цель", client_message_id=uuid4())
    )
    async with env.factory() as db:
        turn = (await db.scalars(select(ChatAgentTurn))).one()
        reply = await db.get(ChatMessage, turn.reply_message_id)
        turn.status, turn.sources, reply.content = (
            "completed",
            encode_answer_sources(answer),
            answer.answer,
        )
        await db.commit()
        reply_id = reply.id
    # Этот payload одинаков для подписчиков и не знает private repo прав viewer.
    events = await env.service.events(1, 2, after=0)
    broadcast = next(
        event.data["message"]
        for event in events.events
        if event.data.get("message", {}).get("id") == reply_id
    )
    assert broadcast["agent"]["requires_context_validation"] is True
    assert original not in str(broadcast) and "context_guard" not in str(broadcast)
    assert broadcast["agent"]["external_sources"] == []
    current = (await env.service.history(1, 2)).messages[-1]
    assert current.content == original and current.agent.requires_context_validation is False
    assert current.agent.external_sources[0].url == "https://example.org/guide"
    assert current.agent.external_sources[0].state == "stale"  # last observation, no network fetch
    async with env.factory() as db:
        await db.execute(
            update(Project).where(Project.id == 1).values(description_md="Изменённая текущая цель")
        )
        await db.commit()
    stale = (await env.service.history(1, 2)).messages[-1]
    assert (
        stale.content == HIDDEN_CONTEXT_ANSWER and stale.agent.requires_context_validation is False
    )
    assert not stale.agent.sources and not stale.agent.external_sources
    assert not stale.agent.assertion_references and not stale.agent.actions
    async with env.factory() as db:
        persisted = await db.get(ChatMessage, reply_id)
        assert persisted.content == original  # presentation не стирает историю
