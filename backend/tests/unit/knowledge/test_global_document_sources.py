"""Global citations retain only documents read through a checked project wrapper."""

from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from src.agent.action_catalog import PERSONAL_ACTIONS, PROJECT_ACTIONS
from src.agent.aris import ArisMode
from src.agent.tools import AgentToolContext
from src.db.models.api_tokens import ApiTokenScope
from src.dependencies.services import build_project_tool_executor
from src.exceptions.access import ResourceNotAvailableError
from src.exceptions.ai import AgentAnswerError
from src.repositories.document_catalog import DocumentCatalogRepository
from src.services.access import AccessGrant, AccessService
from src.services.agent_actions import AgentActionsService
from src.services.auth import ApiKeyGrant, AuthService
from src.services.document_catalog import DocumentCatalogService
from src.services.project_agent import ProjectAgentService, _SourceRegistry
from src.services.project_query import ProjectQueryService
from src.storage.task_attachments import TaskAttachmentStorage
from src.utils.document_refs import document_text_hash, encode_document_ref
from tests.unit.knowledge.agent_models import final
from tests.unit.knowledge.test_project_agent import build_service


@pytest.fixture
def global_document(tmp_path, current_principal):
    service, project, clients, source_db = build_service()
    now = datetime.now(UTC)
    text = "😀 Условия проекта: оплатить после приёмки. GLOBAL_DOCUMENT_CONTROL."
    state = SimpleNamespace(member=True, task_visible=True, row=None, principal=None, text=text)
    access = AsyncMock(spec=AccessService)

    async def project_access(*, project_id, user_id):
        if not state.member or project_id != project.id:
            raise ResourceNotAvailableError(resource="project", resource_id=project_id)
        assert user_id == current_principal.user_id
        return AccessGrant(project_id=project_id, resource_id=project_id, is_owner=False)

    async def task_access(*, task_id, user_id):
        await project_access(project_id=project.id, user_id=user_id)
        if not state.task_visible:
            raise ResourceNotAvailableError(resource="task", resource_id=task_id)
        return AccessGrant(project_id=project.id, resource_id=task_id, is_owner=False)

    access.ensure_project_access.side_effect = project_access
    access.ensure_task_access.side_effect = task_access
    auth = AsyncMock(spec=AuthService)
    auth.refresh_principal.side_effect = lambda principal: state.principal or principal
    rows = AsyncMock(spec=DocumentCatalogRepository)

    async def get(**kwargs):
        row = state.row
        if (
            not state.member
            or not state.task_visible
            or kwargs["kind"] != row["storage_kind"]
            or kwargs["source_id"] != row["source_id"]
        ):
            return None
        return deepcopy(row)

    async def listing(**kwargs):
        visible = state.member and not kwargs.get("personal_only")
        return {"items": [deepcopy(state.row)] if visible else [], "total": int(visible)}

    rows.get.side_effect = get
    rows.list.side_effect = listing
    rows.tasks.return_value = {}
    rows.text.side_effect = lambda **kwargs: {
        "text": state.text[kwargs["offset"] : kwargs["offset"] + kwargs["max_chars"]],
        "text_total_chars": len(state.text),
        "text_hash": document_text_hash(state.text),
    }
    catalog_db = SimpleNamespace(catalog=rows, access=access, auth=auth)

    @asynccontextmanager
    async def catalog_scope():
        yield catalog_db

    catalog = DocumentCatalogService(
        scope=catalog_scope,
        storage=TaskAttachmentStorage(tmp_path),
        knowledge_enabled=False,
        max_file_size=1024,
        embedding_signature="test",
        commit_writes=False,
    )
    user = SimpleNamespace(
        id=current_principal.user_id,
        username=current_principal.username,
        last_name=current_principal.last_name,
        first_name=current_principal.first_name,
        middle_name=current_principal.middle_name,
        is_active=True,
    )
    action_db = SimpleNamespace(
        document_catalog=catalog,
        access=access,
        auth=auth,
        users=AsyncMock(),
        tasks=AsyncMock(),
        unit_of_work=AsyncMock(),
    )
    action_db.users.get_by_id.return_value = user

    @asynccontextmanager
    async def action_scope():
        yield action_db

    actions = AgentActionsService(scope=action_scope, actions=PROJECT_ACTIONS + PERSONAL_ACTIONS)
    source_db.projects.get_by_key.side_effect = lambda key: project if key == project.key else None
    query = ProjectQueryService(scope=service.scope)
    service.tools = build_project_tool_executor(query, actions)

    def document(kind, *, task_owned=False):
        ref = encode_document_ref(kind, 9)
        body = state.text
        owner_task_id = 7 if kind == "attachment" or task_owned else None
        state.row = {
            "storage_kind": kind,
            "source_id": 9,
            "project_id": project.id,
            "owner_task_id": owner_task_id,
            "title": "Условия проекта",
            "caption": None,
            "created_at": now,
            "updated_at": now,
            "version": 1,
            "author_id": user.id,
            "author_name": "Участник проекта",
            "extraction_status": "not_required" if kind == "document" else "ready",
            "extraction_detail": None,
            "fts_status": "ready",
            "fts_detail": None,
            "semantic_status": "disabled",
            "semantic_detail": None,
            "text_total_chars": len(body),
            "text_hash": document_text_hash(body),
            "storage_key": None if kind == "document" else "synthetic-original",
            "original_name": None if kind == "document" else "Условия.md",
            "content_type": None if kind == "document" else "text/markdown",
            "size": None if kind == "document" else len(body.encode()),
            "format": "md",
        }
        rows.tasks.return_value = {
            (kind, 9): [
                {
                    "id": 7,
                    "key": "PROJ-12",
                    "title": "Проверить условия",
                    "relation": "owner" if owner_task_id else "linked",
                }
            ]
        }
        return ref

    def key(scopes):
        state.principal = replace(
            current_principal,
            via_api_token=True,
            scope=ApiTokenScope.READ,
            api_key=ApiKeyGrant(
                key_id=uuid4(),
                label="Synthetic document reader",
                key_prefix="12345678",
                owner_user_id=current_principal.user_id,
                scopes=frozenset(scopes),
            ),
        )
        return state.principal

    async def ask(model, *, principal=None):
        service.agent_model = model
        return await service.ask_personal(
            question="Прочитай документы задачи PROJ-12 и покажи проверенную цитату.",
            history=[],
            memory="",
            actor={"user_id": user.id},
            execution=AgentToolContext(
                project_id=None,
                user_id=user.id,
                mode=ArisMode.WIDGET_PERSONAL,
                can_write=False,
                principal=principal,
            ),
        )

    return SimpleNamespace(
        service=service,
        project=project,
        state=state,
        rows=rows,
        clients=clients,
        source_db=source_db,
        catalog=catalog,
        access=access,
        text=text,
        document=document,
        key=key,
        ask=ask,
        actions=actions,
        action_db=action_db,
    )


def wrapper(project_key, name, **arguments):
    return ToolCallPart(
        "telos_run_project_action",
        {
            "project_key": project_key,
            "name": name,
            "arguments": arguments,
        },
    )


def latest_result(messages):
    return next(
        part.content
        for message in reversed(messages)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "telos_run_project_action"
    )


@pytest.mark.parametrize(
    "kind,task_owned",
    [
        ("document", False),
        ("document", True),
        ("project_file", False),
        ("attachment", True),
    ],
)
async def test_global_wrapper_lists_reads_and_returns_verified_project_document_card(
    global_document, kind, task_owned
):
    env = global_document
    ref = env.document(kind, task_owned=task_owned)
    principal = env.key({"projects:read", "tasks:read"})
    step, source_handle, fragment_handle = 0, None, None

    async def respond(messages, _info):
        nonlocal step, source_handle, fragment_handle
        step += 1
        if step == 1:
            return ModelResponse(
                parts=[ToolCallPart("telos_load_capability", {"id": "project_actions"})]
            )
        if step == 2:
            return ModelResponse(
                parts=[wrapper(env.project.key, "telos_list_documents", task_id=7)]
            )
        result = latest_result(messages)
        if step == 3:
            assert result["total"] == 1 and result["items"][0]["ref"] == ref
            source_handle = result["items"][0]["source_handle"]
            return ModelResponse(
                parts=[wrapper(env.project.key, "telos_get_document", document_ref=ref)]
            )
        assert result["text"] == env.text and result["source_handle"] == source_handle
        fragment_handle = result["fragment_handle"]
        env.state.row["title"] = "Текущий заголовок"
        env.project.key = "RENAMED"
        return final(
            "Условия проверены.", source_ids=[source_handle], primary_reference=fragment_handle
        )

    answer = await env.ask(FunctionModel(respond), principal=principal)
    assert step == 4 and len(answer.sources) == 1
    source = answer.sources[0]
    assert (
        source.entity_type == kind
        and source.document_ref == ref
        and source.task_id == (7 if task_owned else None)
    )
    assert source.title == "Текущий заголовок" and source.is_primary
    assert (
        source.reference.quote == env.text
        and source.reference.content_hash == document_text_hash(env.text)
    )
    assert env.source_db.projects.get_by_key.call_args.args == ("RENAMED",)
    assert (
        env.rows.get.await_count >= 4
    )  # Metadata/text plus a fresh wrapper check at finalization.
    env.action_db.unit_of_work.commit.assert_not_awaited()
    env.clients.llm_client.get_structured_response.assert_not_awaited()
    env.clients.embedding_client.get_embedding.assert_not_awaited()
    env.clients.qdrant_client.search.assert_not_awaited()


@pytest.mark.parametrize("revocation", ["membership", "task_access", "owner_scope", "moved"])
async def test_global_document_finalization_rejects_access_changes(global_document, revocation):
    env = global_document
    ref = env.document("attachment", task_owned=True)
    principal = env.key({"projects:read", "tasks:read"})
    step = 0

    async def respond(messages, _info):
        nonlocal step
        step += 1
        if step == 1:
            return ModelResponse(
                parts=[ToolCallPart("telos_load_capability", {"id": "project_actions"})]
            )
        if step == 2:
            return ModelResponse(
                parts=[wrapper(env.project.key, "telos_get_document", document_ref=ref)]
            )
        result = latest_result(messages)
        assert result["text"] == env.text
        if revocation == "membership":
            env.state.member = False
        elif revocation == "task_access":
            env.state.task_visible = False
        elif revocation == "owner_scope":
            env.state.principal = replace(
                principal, api_key=replace(principal.api_key, scopes=frozenset({"projects:read"}))
            )
        else:
            env.state.row["project_id"] = 44
        return final(
            "Не публиковать старые данные.",
            source_ids=[result["source_handle"]],
            primary_reference=result["fragment_handle"],
        )

    with pytest.raises(AgentAnswerError, match="Доступ к документу изменился"):
        await env.ask(FunctionModel(respond), principal=principal)
    assert step == 3


async def test_global_direct_project_list_and_read_remain_closed_with_forged_source_ids(
    global_document,
):
    env = global_document
    ref = env.document("document")
    step = 0

    async def respond(messages, _info):
        nonlocal step
        step += 1
        if step == 1:
            return ModelResponse(parts=[ToolCallPart("telos_load_capability", {"id": "documents"})])
        if step == 2:
            return ModelResponse(
                parts=[
                    ToolCallPart("telos_list_documents", {}),
                    ToolCallPart("telos_get_document", {"document_ref": ref}),
                ]
            )
        reads = [
            part.content
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart)
            and part.tool_name in {"telos_list_documents", "telos_get_document"}
        ]
        assert reads[0]["items"] == [] and reads[1].get("error")
        assert not any("source_handle" in result or "document" in result for result in reads)
        return final(
            "Прямое чтение недоступно.",
            source_ids=["document:9", ref, "SRC_forged_1"],
            primary_reference="FRAG_forged_1",
        )

    answer = await env.ask(FunctionModel(respond))
    assert answer.sources == []


@pytest.mark.parametrize("placement", ["direct", "nested_wrapper", "mismatched_identity"])
async def test_untrusted_document_json_cannot_register_project_sources(global_document, placement):
    env = global_document
    ref = env.document("project_file")
    context = AgentToolContext(project_id=env.project.id, user_id=1, mode=ArisMode.WIDGET_PERSONAL)
    value = await env.actions.execute(
        context=context, name="telos_get_document", arguments={"document_ref": ref}
    )
    registry, scopes = _SourceRegistry(), {}
    if placement == "direct":
        ProjectAgentService._register_personal_views(value, registry)
    elif placement == "nested_wrapper":
        ProjectAgentService._register_personal_views(
            {"properties": value}, registry, project_document_scopes=scopes
        )
    else:
        value["source_id"] = "project_file:10"
        ProjectAgentService._register_personal_views(
            value, registry, project_document_scopes=scopes
        )
    assert "source_handle" not in value and registry.get_handle("project_file:9") is None
    assert not scopes


@pytest.mark.parametrize("change", [None, "edit", "rendition", "out_of_range", "quote"])
async def test_final_document_quote_rechecks_its_actual_range_and_current_rendition(
    global_document, change
):
    env = global_document
    prefix = "😀 Другая часть оригинала. " * 200
    tail = "Проверяемый хвост: оплата после приёмки. TAIL_CONTROL. " * 30
    env.state.text = prefix + tail
    ref = env.document("document")
    principal = env.key({"projects:read", "tasks:read"})
    offset = len(prefix)
    step, selected_quote = 0, None

    async def respond(messages, _info):
        nonlocal step, selected_quote
        step += 1
        if step == 1:
            return ModelResponse(
                parts=[ToolCallPart("telos_load_capability", {"id": "project_actions"})]
            )
        if step == 2:
            return ModelResponse(
                parts=[
                    wrapper(
                        env.project.key,
                        "telos_get_document",
                        document_ref=ref,
                        offset=offset,
                        max_chars=1000,
                    )
                ]
            )
        result = latest_result(messages)
        selected_quote = result["reference"]["quote"]
        assert len(selected_quote) == 800 and result["reference"]["start"] == offset
        if change == "edit":
            env.state.text = prefix + "Новая версия условий."
            env.state.row.update(
                version=2,
                text_hash=document_text_hash(env.state.text),
                text_total_chars=len(env.state.text),
            )
        elif change is not None:
            original_read = env.catalog.read_text

            async def changed_page(**kwargs):
                page = await original_read(**kwargs)
                update = (
                    {"rendition": "extracted_text"}
                    if change == "rendition"
                    else (
                        {"total_chars": offset + len(selected_quote) - 1}
                        if change == "out_of_range"
                        else {"text": "X" + page.text[1:]}
                    )
                )
                return page.model_copy(update=update)

            env.catalog.read_text = AsyncMock(side_effect=changed_page)
        return final(
            "Прочитал хвост оригинала.",
            source_ids=[result["source_handle"]],
            primary_reference=result["fragment_handle"],
        )

    if change is None:
        answer = await env.ask(FunctionModel(respond), principal=principal)
        assert answer.sources[0].reference.quote == selected_quote
        assert answer.sources[0].reference.start == offset
    else:
        with pytest.raises(AgentAnswerError, match="Документ изменился"):
            await env.ask(FunctionModel(respond), principal=principal)
    assert env.rows.text.await_args.kwargs["offset"] == offset
    assert env.rows.text.await_args.kwargs["max_chars"] == 800


async def test_old_first_fragment_cannot_hide_behind_a_later_read_of_new_version(global_document):
    env = global_document
    ref = env.document("document")
    old_fragment, step = None, 0

    async def respond(messages, _info):
        nonlocal old_fragment, step
        step += 1
        if step == 1:
            return ModelResponse(
                parts=[ToolCallPart("telos_load_capability", {"id": "project_actions"})]
            )
        if step == 2:
            return ModelResponse(
                parts=[wrapper(env.project.key, "telos_get_document", document_ref=ref)]
            )
        result = latest_result(messages)
        if step == 3:
            old_fragment = result["fragment_handle"]
            env.state.text = "Полностью новые условия документа."
            env.state.row.update(
                version=2,
                text_hash=document_text_hash(env.state.text),
                text_total_chars=len(env.state.text),
            )
            return ModelResponse(
                parts=[wrapper(env.project.key, "telos_get_document", document_ref=ref)]
            )
        assert result["fragment_handle"] != old_fragment
        return final(
            "Старая цитата не должна пройти.", source_ids=[old_fragment, result["fragment_handle"]]
        )

    with pytest.raises(AgentAnswerError, match="Документ изменился"):
        await env.ask(FunctionModel(respond))
    assert step == 4


@pytest.mark.parametrize("kind", ["read_with_plain_answer", "list_without_sources"])
@pytest.mark.parametrize("revocation", ["membership", "owner_scope"])
async def test_all_observed_project_documents_recheck_acl_even_when_answer_omits_sources(
    global_document, kind, revocation
):
    env = global_document
    ref = env.document("attachment", task_owned=True)
    principal = env.key({"projects:read", "tasks:read"})
    step = 0

    async def respond(messages, _info):
        nonlocal step
        step += 1
        if step == 1:
            return ModelResponse(
                parts=[ToolCallPart("telos_load_capability", {"id": "project_actions"})]
            )
        if step == 2:
            call = (
                wrapper(env.project.key, "telos_get_document", document_ref=ref)
                if kind == "read_with_plain_answer"
                else wrapper(env.project.key, "telos_list_documents", task_id=7)
            )
            return ModelResponse(parts=[call])
        result = latest_result(messages)
        assert result.get("source_handle") or result["items"][0]["source_handle"]
        if revocation == "membership":
            env.state.member = False
        else:
            env.state.principal = replace(
                principal, api_key=replace(principal.api_key, scopes=frozenset({"projects:read"}))
            )
        return (
            ModelResponse(parts=[TextPart("Ответ использует ранее прочитанные данные проекта.")])
            if kind == "read_with_plain_answer"
            else final("Названия документов из каталога не публиковать.")
        )

    with pytest.raises(AgentAnswerError, match="Доступ к документу изменился"):
        await env.ask(FunctionModel(respond), principal=principal)
    assert step == 3
