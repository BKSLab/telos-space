"""Описания версий не заменяют оригиналы и не требуют повторных расходов."""

import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from src.clients.llm import LlmClient
from src.exceptions.clients import LlmClientError
from src.exceptions.knowledge import KnowledgeProviderError
from src.knowledge.catalog import build_catalog
from src.knowledge.context import read_catalog, retrieve, source_brief
from src.schemas.knowledge import KnowledgeReadRequest
from src.services.source_summaries import SourceSummariesService, SourceSummaryOutput
from tests.unit.knowledge.test_knowledge_index import base_rows, build_service, sync


async def test_summary_reads_all_parts_including_headings_and_long_unbroken_lines():
    rows = base_rows()
    rows["documents"][0]["content_md"] = "# Уникальная тема\n" + "Я" * 12000 + "\nФинальное условие"
    source = build_catalog(1, rows).sources["document:9"]
    client = AsyncMock(spec=LlmClient)
    client.get_structured_response.return_value = SourceSummaryOutput(summary="Темы документа.")
    service = SourceSummariesService(llm_client=client, chunk_chars=2000)
    result = await service.summarize(source)
    parts = [
        json.loads(call.kwargs["content"])
        for call in client.get_structured_response.await_args_list
    ]
    assert all(len(part["text"]) <= 2000 for part in parts)
    assert "".join(part["text"] for part in parts) == source.text
    assert parts[-1]["previous_summary"] == "Темы документа."
    assert result["summary"] == "Темы документа."
    assert result["content_hash"] == source.summary_hash


async def test_short_document_does_not_call_model():
    source = build_catalog(1, base_rows()).sources["document:9"]
    client = AsyncMock(spec=LlmClient)
    result = await SourceSummariesService(llm_client=client, chunk_chars=16000).summarize(source)
    assert "Требования к изделию" in result["summary"]
    client.get_structured_response.assert_not_awaited()


async def test_index_reuses_description_until_content_changes(tmp_path):
    rows = base_rows()
    rows["documents"][0]["content_md"] = "Содержание документа. " * 200
    service = build_service(tmp_path, rows=rows)
    try:
        action = await sync(service, rows)
        client = service.summarizer.llm_client
        client.get_structured_response.assert_awaited_once()
        service.summaries_repository.save.assert_awaited_once()
        rows["knowledge_source_summaries"] = action.summaries
        client.reset_mock()
        rows["document_links"] = []
        await sync(service, rows)
        client.get_structured_response.assert_not_awaited()
        rows["documents"][0]["content_md"] += " Изменённое условие."
        assert build_catalog(1, rows).sources["document:9"].summary is None
        await sync(service, rows)
        client.get_structured_response.assert_awaited_once()
    finally:
        await service.qdrant_client.close()


async def test_failed_summary_keeps_original_index_and_successful_other_summaries(tmp_path):
    rows = base_rows()
    rows["documents"].append(
        {**rows["documents"][0], "id": 10, "slug": "long", "content_md": "Большой текст. " * 1000}
    )
    service = build_service(tmp_path, rows=rows)
    service.summarizer.llm_client.get_structured_response.side_effect = LlmClientError("offline")
    try:
        with pytest.raises(KnowledgeProviderError, match="Описание document:10"):
            await sync(service, rows)
        assert service.summaries_repository.save.await_args.args[0]["source_id"] == "document:9"
        manifest = await service.qdrant_client.manifest(1)
        assert any(item["source_id"] == "document:10" for item in manifest.values())
    finally:
        await service.qdrant_client.close()


def test_summary_selects_document_but_original_tail_remains_readable():
    rows = base_rows()
    rows["documents"][0]["content_md"] = (
        "Описание поставки. " * 5000 + "Алмазокомплект должен поступить 25 октября."
    )
    catalog = build_catalog(1, rows)
    source = catalog.sources["document:9"]
    rows["knowledge_source_summaries"] = [
        {
            "project_id": 1,
            "source_id": source.source_id,
            "content_hash": source.summary_hash,
            "summary": "Условия поставки оборудования.",
        }
    ]
    catalog = build_catalog(1, rows)
    result = retrieve(
        catalog,
        fts_hits=[{"source_id": "document:9"}],
        semantic_hits=[],
        query="Алмазокомплект",
        target_chars=2200,
        overlap_chars=300,
    )[0]
    assert result["content_kind"] == "summary"
    assert "25 октября" not in json.dumps(result, ensure_ascii=False)
    offset = result["matched_locations"][0]["offset"]
    original = read_catalog(
        catalog, KnowledgeReadRequest(name="telos_read_source", source_id="document:9", offset=offset)
    )
    assert "25 октября" in original["text"]
    assert original["next_offset"] is None
    # Чужой или устаревший кэш не влияет на ответы.
    changed = deepcopy(rows)
    changed["knowledge_source_summaries"][0]["project_id"] = 2
    assert (
        source_brief(build_catalog(1, changed).sources["document:9"])["summary_status"] == "pending"
    )
