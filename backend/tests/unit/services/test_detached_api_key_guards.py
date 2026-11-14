"""Scoped keys cannot bypass HTTP restrictions through a short DB scope."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.db.models.api_tokens import ApiTokenScope
from src.exceptions.api_keys import ApiKeyRouteNotAllowedError, ApiKeyScopeError
from src.services.attachment_download import AttachmentDownloadService
from src.services.auth import ApiKeyGrant, Principal
from src.services.chat_presenter import ChatPresenter
from src.services.project_chats import ProjectChatService
from src.services.risk_formulation import RiskFormulationService
from src.services.risk_suggestions import RiskSuggestionService
from src.services.task_checklist_suggestions import TaskChecklistSuggestionService


def key_principal(scopes=()):
    return Principal(
        user_id=1,
        username="owner",
        last_name="Owner",
        first_name="Local",
        middle_name=None,
        scope=ApiTokenScope.WRITE,
        via_api_token=True,
        api_key=ApiKeyGrant(
            key_id=uuid4(),
            label="Integration",
            key_prefix="telos_key_test",
            scopes=frozenset(scopes),
            owner_user_id=1,
        ),
    )


@pytest.mark.parametrize("scenario", ["formulation", "risks", "checklist", "chat"])
async def test_scoped_keys_cannot_enter_unlisted_detached_routes(scenario):
    # Even a key with every domain scope has no access to these UI-only routes.
    principal = key_principal(("projects:read", "tasks:read", "tasks:write", "agent:ask"))
    project_access = AsyncMock()
    db = SimpleNamespace(
        auth=SimpleNamespace(resolve_principal=AsyncMock(return_value=principal)),
        access=SimpleNamespace(ensure_project_access=project_access),
    )

    @asynccontextmanager
    async def scope():
        yield db

    llm = AsyncMock()
    credentials = dict(project_id=1, session_token=None, bearer_secret="telos_key_test")
    if scenario == "formulation":
        call = RiskFormulationService(scope=scope, llm_client=llm).suggest(
            **credentials, data=SimpleNamespace()
        )
    elif scenario == "risks":
        call = RiskSuggestionService(scope=scope, llm_client=llm).suggest(**credentials)
    elif scenario == "checklist":
        service = TaskChecklistSuggestionService(
            scope=scope,
            llm_client=llm,
            vision=None,
            document_catalog=AsyncMock(),
            file_context_limit=1000,
            max_file_size=1000,
        )
        call = service.suggest(**credentials, data=SimpleNamespace(), files=[])
    else:
        service = ProjectChatService(scope, ChatPresenter())
        call = service.authenticate(**credentials)
    with pytest.raises(ApiKeyRouteNotAllowedError):
        await call
    project_access.assert_not_awaited()
    llm.get_structured_response.assert_not_awaited()


@pytest.mark.parametrize("scopes, allowed", [((), False), (("tasks:read",), True)])
async def test_attachment_download_requires_task_read_before_loading_file(
    tmp_path, scopes, allowed
):
    principal = key_principal(scopes)
    path = tmp_path / "document.txt"
    path.write_text("local file", encoding="utf-8")
    unit = SimpleNamespace(
        resolve_principal=AsyncMock(return_value=principal),
        ensure_task_access=AsyncMock(),
        load_attachment_content=AsyncMock(
            return_value=SimpleNamespace(
                path=path,
                content_type="text/plain",
                original_name="document.txt",
                previewable=True,
            )
        ),
    )

    @asynccontextmanager
    async def scope():
        yield unit

    call = AttachmentDownloadService(scope=scope).prepare(
        task_id=1, attachment_id=2, session_token=None, bearer_secret="telos_key_test"
    )
    if allowed:
        download = await call
        assert download.path.read_text(encoding="utf-8") == "local file"
        unit.ensure_task_access.assert_awaited_once_with(task_id=1, user_id=1)
    else:
        with pytest.raises(ApiKeyScopeError):
            await call
        unit.ensure_task_access.assert_not_awaited()
        unit.load_attachment_content.assert_not_awaited()
