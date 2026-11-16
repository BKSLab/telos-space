"""Реальные сервисные границы egress: согласие, версии, ACL и короткие DB-области."""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.db.models.api_tokens import ApiTokenScope
from src.exceptions.access import ResourceNotAvailableError
from src.exceptions.web_context import WebResponseError
from src.exceptions.web_research import (
    WebResearchConsentError,
    WebResearchDisabledError,
    WebResearchServiceError,
    WebResearchVersionError,
)
from src.schemas.web_context import WebReadResult, WebSearchResult, WebSource
from src.services.auth import Principal
from src.services.web_research import WebResearchService
from src.utils.web_grants import WebGrantSigner

NOW = datetime(2026, 11, 16, 12, tzinfo=UTC)
URL = "https://example.org/guide"


@pytest.fixture
def research():
    principal = Principal(
        user_id=7,
        username="writer",
        first_name="Тест",
        last_name="Автор",
        middle_name=None,
        scope=ApiTokenScope.WRITE,
        via_api_token=False,
    )
    state = SimpleNamespace(active=False, now=NOW)
    auth = SimpleNamespace(refresh_principal=AsyncMock(return_value=principal))
    access = SimpleNamespace(
        ensure_project_access=AsyncMock(return_value=SimpleNamespace(can_write=True)),
        require_write=lambda grant: None,
    )

    @asynccontextmanager
    async def scope():
        assert not state.active
        state.active = True
        try:
            yield SimpleNamespace(auth=auth, access=access)
        finally:
            state.active = False

    client = SimpleNamespace(search=AsyncMock(), read=AsyncMock())
    documents = SimpleNamespace(
        create=AsyncMock(return_value="saved-document"),
        replay_creation=AsyncMock(return_value=None),
    )
    signer = WebGrantSigner(secret="test-purpose-only", clock=lambda: state.now)
    service = WebResearchService(
        scope=scope,
        client=client,
        signer=signer,
        documents=documents,
        enabled=True,
        search_enabled=True,
    )
    return SimpleNamespace(
        service=service,
        client=client,
        principal=principal,
        access=access,
        state=state,
        documents=documents,
        auth=auth,
        signer=signer,
    )


def page(**changes):
    return WebReadResult(
        source_id="web:" + "a" * 64,
        url=URL,
        requested_url=URL,
        title="Guide",
        text="Text with ``` fences",
        fetched_at=NOW,
        content_hash="b" * 64,
        coverage="extracted_text",
        total_chars=20,
        media_type="text/html",
        downloaded_bytes=100,
        **changes,
    )


async def grant_page(research):
    return await research.service.approve_url(
        project_id=3, principal=research.principal, url=URL, approval_id="approved-human-action"
    )


@pytest.mark.parametrize("mismatch", ["project", "user", "kind", "tamper", "expired"])
def test_grants_are_bound_and_expire(research, mismatch):
    token, _ = research.signer.issue(
        project_id=3, user_id=7, kind="topic", value="public topic", approval_id="action-1"
    )
    args = {"project_id": 3, "user_id": 7, "kind": "topic"}
    if mismatch == "project":
        args["project_id"] = 4
    elif mismatch == "user":
        args["user_id"] = 8
    elif mismatch == "kind":
        args["kind"] = "page"
    elif mismatch == "tamper":
        token += "x"
    else:
        research.state.now += timedelta(seconds=900)
    with pytest.raises(ValueError):
        research.signer.verify(token, **args)


@pytest.mark.parametrize(
    "topic", ["token=private-value", "contact person@example.org", "Bearer private-token"]
)
async def test_secret_and_personal_address_never_receive_permission(research, topic):
    with pytest.raises(WebResearchConsentError):
        await research.service.approve_topic(
            project_id=3, principal=research.principal, public_topic=topic, approval_id="human-1"
        )
    research.client.search.assert_not_awaited()


async def test_search_is_exact_approved_topic_and_only_snippet(research):
    topic = await research.service.approve_topic(
        project_id=3,
        principal=research.principal,
        public_topic="Python official documentation",
        approval_id="human-1",
    )
    source = WebSource(
        source_id="web:" + "a" * 64,
        url=URL,
        title="Guide",
        text="snippet",
        fetched_at=NOW,
        content_hash="c" * 64,
        coverage="search_snippet",
        total_chars=7,
    )

    async def search(request, *, allow_external_query):
        assert not research.state.active
        assert request.query == "Python official documentation" and allow_external_query is True
        return WebSearchResult(fetched_at=NOW, items=[source])

    research.client.search.side_effect = search
    result = await research.service.search(
        project_id=3, principal=research.principal, topic_token=topic.topic_token
    )
    assert result.items[0].coverage == "search_snippet"
    decoded = research.signer.verify(
        result.items[0].page_token, project_id=3, user_id=7, kind="page"
    )
    assert decoded["q"] == URL and decoded["a"] == "human-1"
    assert decoded["exp"] <= int(topic.expires_at.timestamp())
    research.client.read.assert_not_awaited()
    research.documents.create.assert_not_awaited()


async def test_topic_token_is_not_permission_for_arbitrary_page(research):
    topic = await research.service.approve_topic(
        project_id=3, principal=research.principal, public_topic="safe topic", approval_id="human-1"
    )
    with pytest.raises(WebResearchConsentError):
        await research.service.read(
            project_id=3, principal=research.principal, page_token=topic.topic_token
        )
    research.client.read.assert_not_awaited()


async def test_access_revoked_during_network_blocks_publishing_without_db_checkout(research):
    approved = await grant_page(research)

    async def read(request):
        assert not research.state.active
        research.access.ensure_project_access.side_effect = ResourceNotAvailableError(
            resource="Проект", resource_id=3
        )
        return page()

    research.client.read.side_effect = read
    with pytest.raises(ResourceNotAvailableError):
        await research.service.read(
            project_id=3, principal=research.principal, page_token=approved.page_token
        )
    assert not research.state.active


async def test_expired_while_reading_is_not_published(research):
    approved = await grant_page(research)

    async def read(request):
        research.state.now += timedelta(seconds=901)
        return page()

    research.client.read.side_effect = read
    with pytest.raises(WebResearchConsentError):
        await research.service.read(
            project_id=3, principal=research.principal, page_token=approved.page_token
        )


async def test_provider_failure_does_not_include_raw_details(research):
    approved = await grant_page(research)
    research.client.read.side_effect = WebResponseError("provider-private-detail")
    with pytest.raises(WebResearchServiceError) as error:
        await research.service.read(
            project_id=3, principal=research.principal, page_token=approved.page_token
        )
    assert "private-detail" not in error.value.detail


async def test_disabled_search_and_status_do_not_call_network(research):
    research.service.search_enabled = False
    approved = await research.service.approve_topic(
        project_id=3,
        principal=research.principal,
        public_topic="public topic",
        approval_id="human-1",
    )
    status = await research.service.status(project_id=3, principal=research.principal)
    assert status.reading_enabled and not status.search_enabled
    with pytest.raises(WebResearchDisabledError):
        await research.service.search(
            project_id=3, principal=research.principal, topic_token=approved.topic_token
        )
    research.client.search.assert_not_awaited()


async def test_save_rejects_changed_page_and_does_not_trust_model_text(research):
    approved = await grant_page(research)
    research.client.read.return_value = page()
    with pytest.raises(WebResearchVersionError):
        await research.service.save(
            project_id=3,
            principal=research.principal,
            page_token=approved.page_token,
            expected_hash="d" * 64,
            title="Research",
            request_id=uuid4(),
        )
    research.documents.create.assert_not_awaited()


async def test_save_has_server_provenance_and_existing_catalog_receipt(research):
    approved = await grant_page(research)
    research.client.read.return_value = page()
    request_id = uuid4()
    result = await research.service.save(
        project_id=3,
        principal=research.principal,
        page_token=approved.page_token,
        expected_hash="b" * 64,
        title="Research",
        request_id=request_id,
    )
    assert result == "saved-document"
    call = research.documents.create.await_args.kwargs
    data = call["data"]
    assert call["user_id"] == 7 and data.project_id == 3 and data.request_id == request_id
    assert URL in data.content_md and "b" * 64 in data.content_md
    assert "extracted_text" in data.content_md and "2026-11-16" in data.content_md
    assert "````text\nText with ``` fences\n````" in data.content_md


async def test_refresh_of_credentials_is_checked_on_every_phase(research):
    approved = await grant_page(research)
    research.client.read.return_value = page()
    before = research.auth.refresh_principal.await_count
    await research.service.read(
        project_id=3, principal=research.principal, page_token=approved.page_token
    )
    assert research.auth.refresh_principal.await_count == before + 2


async def test_prepared_save_has_no_write_and_persist_has_no_network(research):
    approved = await grant_page(research)
    research.client.read.return_value = page()
    plan = await research.service.prepare_save(
        project_id=3,
        principal=research.principal,
        page_token=approved.page_token,
        expected_hash="b" * 64,
        title="Prepared research",
    )
    research.documents.create.assert_not_awaited()
    bound = SimpleNamespace(create=AsyncMock(return_value="atomic-document"))
    result = await research.service.persist_prepared(
        project_id=3,
        principal=research.principal,
        plan=plan,
        request_id=uuid4(),
        documents=bound,
    )
    assert result == "atomic-document"
    research.client.read.assert_awaited_once()
    research.documents.create.assert_not_awaited()
    bound.create.assert_awaited_once()


async def test_prepared_save_is_bound_and_rechecks_expiry(research):
    approved = await grant_page(research)
    research.client.read.return_value = page()
    plan = await research.service.prepare_save(
        project_id=3,
        principal=research.principal,
        page_token=approved.page_token,
        expected_hash="b" * 64,
        title="Prepared research",
    )
    with pytest.raises(WebResearchConsentError):
        await research.service.persist_prepared(
            project_id=3,
            principal=research.principal,
            plan=replace(plan, project_id=4),
            request_id=uuid4(),
            documents=research.documents,
        )
    research.state.now += timedelta(seconds=901)
    with pytest.raises(WebResearchConsentError):
        await research.service.persist_prepared(
            project_id=3,
            principal=research.principal,
            plan=plan,
            request_id=uuid4(),
            documents=research.documents,
        )
    research.documents.create.assert_not_awaited()


async def test_snippet_grant_check_does_not_pretend_to_read_page(research):
    approved = await grant_page(research)
    await research.service.validate_page_grants(
        project_id=3, principal=research.principal, page_tokens=[approved.page_token]
    )
    research.client.read.assert_not_awaited()
    research.client.search.assert_not_awaited()
    research.state.now += timedelta(seconds=901)
    with pytest.raises(WebResearchConsentError):
        await research.service.validate_page_grants(
            project_id=3, principal=research.principal, page_tokens=[approved.page_token]
        )
