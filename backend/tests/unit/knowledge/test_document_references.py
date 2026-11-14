"""Цитаты адресуют raw версию документа; модель не задаёт доверенный текст."""

import json
from copy import deepcopy

import pytest

from src.agent.tools import AgentToolRequest
from src.knowledge.catalog import build_catalog
from src.knowledge.context import document_fragment, source_brief, source_view
from src.services.project_agent import ProjectAgentService, _SourceRegistry
from src.utils.document_refs import document_text_hash, encode_document_ref
from tests.unit.knowledge.agent_models import LegacyOutput
from tests.unit.knowledge.test_knowledge_index import base_rows
from tests.unit.knowledge.test_project_agent import build_service


def document_rows():
    rows = base_rows()
    text = "😀 Начало\n\n" + "Другой текст. " * 80 + "Искомое условие: оплата после приёмки."
    rows["documents"][0]["content_md"] = text
    rows["task_attachments"] = [{"id": 15, "task_id": 7, "original_name": "условия.md"}]
    rows["knowledge_attachment_texts"] = [{"attachment_id": 15, "text": text, "status": "ready"}]
    rows["project_files"] = [{"id": 16, "project_id": 1, "original_name": "условия.pdf"}]
    rows["knowledge_project_file_texts"] = [{"project_file_id": 16, "text": text, "status": "ready"}]
    return rows, text


def test_document_pages_bind_unicode_ranges_to_viewer_text_in_all_storage_kinds():
    rows, text = document_rows()
    catalog = build_catalog(1, rows)
    raw_start = text.index("Искомое условие")
    for source_id, kind, entity_id, rendition in [
        ("document:9", "document", 9, "markdown"),
        ("attachment:15", "attachment", 15, "extracted_text"),
        ("project_file:16", "project_file", 16, "extracted_text"),
    ]:
        source = catalog.sources[source_id]
        page = source_view(source, offset=source.document_text_start + raw_start, max_chars=500)
        fragment = page["reference"]
        assert fragment == {
            "document_ref": encode_document_ref(kind, entity_id),
            "rendition": rendition,
            "content_hash": document_text_hash(text),
            "start": raw_start,
            "end": len(text),
            "offset_unit": "unicode_codepoints",
            "quote": text[raw_start:],
        }
        assert fragment["start"] != page["offset"]  # Каталог имеет служебный префикс.
        assert source_view(source, offset=0, max_chars=10).get("reference") is None
        assert document_fragment(source, start=-1, end=3) is None
        assert document_fragment(source, start=0, end=len(text) + 1) is None


def test_summary_never_becomes_a_verified_quote_and_forged_reference_is_rejected():
    rows, text = document_rows()
    catalog = build_catalog(1, rows)
    source = catalog.sources["document:9"]
    source.summary = "Модельное резюме, которого в оригинале нет."
    summary = source_brief(source)
    assert summary["content_kind"] == "summary" and "reference" not in summary
    page = source_view(source, offset=source.document_text_start, max_chars=500)
    page["reference"]["quote"] = "X" * len(page["reference"]["quote"])
    registry = _SourceRegistry()
    ProjectAgentService._register_views(page, catalog, registry)
    assert "fragment_handle" not in page
    assert registry.resolve(page["source_handle"]).reference is None
    assert text.startswith("😀")


def test_fragment_handles_are_immutable_distinguish_pages_and_do_not_accept_other_runs():
    rows, text = document_rows()
    catalog = build_catalog(1, rows)
    source = catalog.sources["document:9"]
    registry = _SourceRegistry()
    first = source_view(source, offset=source.document_text_start, max_chars=500)
    ProjectAgentService._register_views(first, catalog, registry)
    first_reference = registry.resolve_reference(first["fragment_handle"]).reference
    tail_start = text.index("Искомое условие")
    last = source_view(source, offset=source.document_text_start + tail_start, max_chars=500)
    ProjectAgentService._register_views(last, catalog, registry)
    assert first["source_handle"] == last["source_handle"]
    assert first["fragment_handle"] != last["fragment_handle"]
    assert registry.resolve_reference(first["fragment_handle"]).reference == first_reference
    assert registry.resolve_reference(last["fragment_handle"]).reference.quote == text[tail_start:]
    assert _SourceRegistry().resolve_reference(last["fragment_handle"]) is None
    assert registry.resolve_reference("document:9") is None
    assert registry.resolve_reference("FRAG_invented_1") is None

    changed = deepcopy(rows)
    changed["documents"][0]["content_md"] = "Новая версия"
    current = build_catalog(1, changed).sources["document:9"]
    assert document_fragment(current, start=0, end=len(current.document_text)).content_hash != first_reference.content_hash


@pytest.mark.asyncio
async def test_answer_selects_verified_primary_among_sources_and_rechecks_current_catalog():
    for deleted in (False, True):
        service, _, clients, db = build_service()
        rows, text = document_rows()
        db.sources.get_project_rows.side_effect = None
        db.sources.get_project_rows.return_value = rows
        source = build_catalog(1, rows).sources["document:9"]
        tail_offset = source.document_text_start + text.index("Искомое условие")

        async def respond(*, content, rows=rows, source=source, tail_offset=tail_offset, deleted=deleted, **_kwargs):
            payload = json.loads(content)
            if not payload.get("read_results"):
                return LegacyOutput(tool_calls=[
                    AgentToolRequest(name="telos_read_source", arguments={"source_id": "document:9", "offset": tail_offset}),
                    AgentToolRequest(name="telos_read_source", arguments={"source_id": "document:9", "offset": source.document_text_start}),
                ])
            first, second = [item["result"] for item in payload["read_results"]]
            task_handle = next(item["source_handle"] for item in payload["retrieval_context"] if item["source_id"] == "task:7")
            if deleted:
                rows["documents"] = []
            else:
                rows["documents"][0]["title"] = "Текущий заголовок"
                rows["documents"][0]["content_md"] = "Новый текст"
            return LegacyOutput(
                answer="Проверены условия.", source_ids=[task_handle, second["source_handle"]],
                primary_reference=first["fragment_handle"],
            )

        clients.llm_client.get_structured_response.side_effect = respond
        result = await service.ask(project_id=1, question="Какие условия приёмки?", history=[])
        documents = [item for item in result.sources if item.entity_type == "document"]
        if deleted:
            assert documents == [] and not any(item.is_primary for item in result.sources)
        else:
            assert len(result.sources) == 2 and len(documents) == 1
            assert documents[0].is_primary and documents[0].title == "Текущий заголовок"
            assert documents[0].reference.quote == text[text.index("Искомое условие"):]
            assert documents[0].reference.content_hash == document_text_hash(text)
            assert documents[0].reference.content_hash != document_text_hash("Новый текст")
