"""Настоящий PostgreSQL: исследование, серверный автор и устойчивый replay."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from src.core.settings import get_settings
from src.db.models.documents import Document
from src.dependencies.document_catalog import build_document_catalog_scope
from src.exceptions.document_catalog import CatalogDocumentConflictError
from src.schemas.web_context import WebReadResult
from src.services.document_catalog import DocumentCatalogService
from src.services.web_research import WebResearchService
from src.utils.document_refs import decode_document_ref
from src.utils.tokens import create_access_token
from src.utils.web_grants import WebGrantSigner
from tests.integration.repositories.test_file_processing_search import world as _research_world

research_world = _research_world


async def test_saved_research_replays_before_network_and_keeps_first_provenance(research_world):
    world = research_world
    settings = get_settings()
    scope = build_document_catalog_scope(session_factory=world.sessions, settings=settings)
    documents = DocumentCatalogService(
        scope=scope,
        storage=world.storage,
        knowledge_enabled=False,
        max_file_size=1024 * 1024,
        embedding_signature="test-model",
    )
    principal = await documents.resolve_principal(
        session_token=create_access_token(world.owner), bearer_secret=None
    )
    now = datetime.now(UTC)
    source = WebReadResult(
        source_id="web:" + "a" * 64,
        url="https://example.org/guide",
        requested_url="https://example.org/guide",
        title="Guide",
        text="Verified text",
        fetched_at=now,
        content_hash="b" * 64,
        coverage="extracted_text",
        total_chars=13,
        media_type="text/plain",
        downloaded_bytes=13,
    )
    client = SimpleNamespace(read=AsyncMock(return_value=source))
    research = WebResearchService(
        scope=scope,
        client=client,
        signer=WebGrantSigner(secret="test-signing-only"),
        documents=documents,
        enabled=True,
        search_enabled=False,
    )
    approved = await research.approve_url(
        project_id=world.project,
        principal=principal,
        url="https://EXAMPLE.org/guide#part",
        approval_id="human",
    )
    request_id = uuid4()
    args = dict(
        project_id=world.project,
        principal=principal,
        page_token=approved.page_token,
        expected_hash="b" * 64,
        title="Public research",
        request_id=request_id,
    )
    first = await research.save(**args)
    client.read.return_value = source.model_copy(update={"fetched_at": now + timedelta(seconds=2)})
    plan = await research.prepare_save(
        **{key: value for key, value in args.items() if key != "request_id"}
    )
    second = await research.persist_prepared(
        project_id=world.project,
        principal=principal,
        plan=plan,
        request_id=request_id,
        documents=documents,
    )
    assert second.ref == first.ref
    client.read.side_effect = AssertionError("Завершённый повтор не должен обращаться к странице")
    replay = await research.save(**args)
    assert replay.ref == first.ref and client.read.await_count == 2
    with pytest.raises(CatalogDocumentConflictError):
        await research.save(**{**args, "title": "Другой запрос с тем же UUID"})
    _, document_id = decode_document_ref(first.ref)
    async with world.sessions() as db:
        stored = await db.get(Document, document_id)
        assert stored.authored_by_user_id == world.owner
        assert "https://example.org/guide" in stored.content_md and "b" * 64 in stored.content_md
        assert now.isoformat() in stored.content_md
        assert (now + timedelta(seconds=2)).isoformat() not in stored.content_md
        assert (
            await db.scalar(
                select(func.count())
                .select_from(Document)
                .where(Document.slug == f"doc-{request_id.hex}")
            )
        ) == 1
