"""Catalog text metadata is not a quote; native project reads obtain the actual text."""

# ruff: noqa: F811 -- pytest injects the imported fixture by its parameter name.

from dataclasses import replace

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from src.agent.aris import ArisMode
from src.agent.tools import AgentToolContext
from src.knowledge.catalog import build_catalog
from src.services.project_agent import ProjectAgentService, _SourceRegistry
from src.utils.document_refs import document_text_hash
from tests.unit.knowledge.agent_models import final
from tests.unit.knowledge.test_document_references import document_rows
from tests.unit.knowledge.test_global_document_sources import global_document as global_document
from tests.unit.knowledge.test_knowledge_index import base_rows


@pytest.mark.parametrize(
    "text,chunks,expected",
    [
        ({"available": True, "total_chars": 80, "rendition": "markdown"}, None, ""),
        (None, [{"text": {"available": True}}], ""),
        (["metadata"], [None, "metadata", {"text": 12}], ""),
        (True, [{"text": None}], ""),
        ("Страница оригинала", {"text": "Это не список совпадений"}, "Страница оригинала"),
        ("Страница оригинала", [{"text": {"available": True}}], "Страница оригинала"),
        (
            {"available": True},
            [{"text": {}}, {"text": "Текст поискового совпадения"}],
            "Текст поискового совпадения",
        ),
    ],
)
def test_source_registration_quotes_only_string_text_and_matching_chunks(text, chunks, expected):
    rows, _ = document_rows()
    catalog = build_catalog(1, rows)
    registry = _SourceRegistry()
    value = {"source_id": "document:9", "text": text, "matching_chunks": chunks}

    ProjectAgentService._register_views(value, catalog, registry)

    source = registry.resolve(value["source_handle"])
    assert source.excerpt == expected
    assert source.reference is None and "fragment_handle" not in value
    assert value["text"] is text and value["matching_chunks"] is chunks


@pytest.mark.parametrize("mode", [ArisMode.PROJECT_CHAT, ArisMode.WIDGET_PROJECT])
@pytest.mark.parametrize("kind", ["document", "project_file", "attachment"])
async def test_native_project_catalog_metadata_then_document_tail_returns_verified_card(
    global_document, mode, kind
):
    env = global_document
    prefix = "😀 Начало документа проекта. " * 180
    tail = "Проверенный хвост оригинала. PROJECT_DOCUMENT_TAIL_CONTROL."
    env.state.text = prefix + tail
    ref = env.document(kind, task_owned=kind == "attachment")
    rows = base_rows()
    rows["documents"] = []
    rows["document_links"] = []
    if kind == "document":
        rows["documents"] = [
            {
                "id": 9,
                "project_id": env.project.id,
                "slug": "native-spec",
                "title": "Условия проекта",
                "content_md": env.state.text,
                "version": 1,
            }
        ]
        rows["document_links"] = [{"id": 1, "document_id": 9, "task_id": 7}]
    elif kind == "project_file":
        rows["project_files"] = [
            {
                "id": 9,
                "project_id": env.project.id,
                "original_name": "Условия.md",
                "version": 1,
            }
        ]
        rows["knowledge_project_file_texts"] = [
            {"project_file_id": 9, "text": env.state.text, "status": "ready"}
        ]
        rows["document_links"] = [{"id": 1, "project_file_id": 9, "task_id": 7}]
    else:
        rows["task_attachments"] = [
            {"id": 9, "task_id": 7, "original_name": "Условия.md", "version": 1}
        ]
        rows["knowledge_attachment_texts"] = [
            {"attachment_id": 9, "text": env.state.text, "status": "ready"}
        ]
    env.source_db.sources.get_project_rows.side_effect = None
    env.source_db.sources.get_project_rows.return_value = rows
    env.service.config = replace(env.service.config, knowledge_enabled=False)
    step, listed_handle, fragment_handle = 0, None, None

    async def respond(messages, _info):
        nonlocal step, listed_handle, fragment_handle
        step += 1
        if step == 1:
            return ModelResponse(parts=[ToolCallPart("telos_load_capability", {"id": "documents"})])
        if step == 2:
            return ModelResponse(
                parts=[ToolCallPart("telos_list_documents", {"task_id": 7, "scope": "all"})]
            )
        if step == 3:
            result = next(
                part.content
                for message in reversed(messages)
                for part in message.parts
                if isinstance(part, ToolReturnPart) and part.tool_name == "telos_list_documents"
            )
            item = result["items"][0]
            assert item["ref"] == ref and isinstance(item["text"], dict)
            assert item["text"]["available"] is True
            listed_handle = item["source_handle"]
            assert "fragment_handle" not in item
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "telos_get_document",
                        {"document_ref": ref, "offset": len(prefix), "max_chars": 500},
                    )
                ]
            )
        result = next(
            part.content
            for message in reversed(messages)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "telos_get_document"
        )
        assert isinstance(result["text"], str) and result["text"] == tail
        assert result["source_handle"] == listed_handle
        fragment_handle = result["fragment_handle"]
        assert result["reference"]["quote"] == tail
        assert result["reference"]["start"] == len(prefix)
        return final(
            "Проверены условия в конце оригинала.",
            source_ids=[listed_handle],
            primary_reference=fragment_handle,
        )

    env.service.agent_model = FunctionModel(respond)
    answer = await env.service.ask(
        project_id=env.project.id,
        question="Прочитай документы задачи PROJ-12 и процитируй хвост оригинала.",
        history=[],
        actor={"user_id": 1},
        mode=mode,
        execution=AgentToolContext(
            project_id=env.project.id, user_id=1, can_write=False, mode=mode
        ),
    )

    assert step == 4 and len(answer.sources) == 1
    source = answer.sources[0]
    assert source.document_ref == ref and source.entity_type == kind and source.is_primary
    assert source.excerpt == tail and source.reference.quote == tail
    assert source.reference.start == len(prefix)
    assert source.reference.content_hash == document_text_hash(env.state.text)
    assert env.rows.list.await_count == 1 and env.rows.text.await_count == 1
    env.clients.llm_client.get_structured_response.assert_not_awaited()
    env.clients.embedding_client.get_embedding.assert_not_awaited()
    env.clients.qdrant_client.search.assert_not_awaited()
