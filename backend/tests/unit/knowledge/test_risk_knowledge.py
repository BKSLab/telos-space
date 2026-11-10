"""Проверка актуальности применима ко всем источникам, включая риски."""

import json

import pytest

from src.clients.qdrant import KnowledgeSearchHit
from src.knowledge.catalog import POLICY_BY_TYPE, SourceType, build_catalog
from src.knowledge.context import read_catalog, retrieve
from src.schemas.knowledge import KnowledgeReadRequest
from tests.unit.knowledge.test_knowledge_index import base_rows


def test_risk_includes_both_plans_and_is_a_link_not_a_task_child():
    catalog = build_catalog(1, base_rows())
    source = catalog.sources["risk:12"]
    chunks = source.chunks(250, 30)
    text = "\n".join(chunk.text for chunk in chunks)
    assert "Превентивные меры" in text and "Резервный адаптер" in text
    assert all(
        chunk.payload["task_id"] is None and chunk.payload["task_ids"] == ["7"] for chunk in chunks
    )
    assert chunks == source.chunks(250, 30)


@pytest.mark.parametrize("kind", list(SourceType))
def test_every_semantic_source_is_validated_and_never_trusts_stale_or_foreign_text(kind):
    rows = base_rows()
    rule = POLICY_BY_TYPE[kind]
    if kind == SourceType.PROJECT:
        source_id = "project:1"
        rows["projects"][0]["description_md"] = "Актуальный паспорт"
    else:
        record = {
            "id": 21,
            "project_id": 1,
            "task_id": 7,
            "stage_id": 3,
            "number": 21,
            "revision": 1,
            "title": "Актуальный источник",
            "name": "Актуальный источник",
            "body": "Актуальный источник",
            "author_name": "Автор",
            "body_md": "Актуальный источник",
            "description_md": "Актуальный источник",
            "description": "Актуальный источник",
            "content_md": "Актуальный источник",
            "original_name": "Актуальный источник.txt",
            "event_type": "DESCRIPTION_CHANGED",
            "comment": "Актуальный источник",
            "to_value": "Актуальный источник",
            "payload": {"text": "Актуальный источник"},
            "created_at": "2026-09-07",
            "role": "OWNER",
            "user_id": 99,
        }
        rows[rule.table] = [record]
        if kind is SourceType.ANALYTICS_REPORT:
            record["context_summary"] = {"snapshot": "Исходная предпосылка"}
        rows["projects"][0]["owner_id"] = 99
        rows["users"] = [{"id": 99, "username": "anna", "first_name": "Актуальный источник"}]
        source_id = f"{kind}:21"
    catalog = build_catalog(1, rows)
    hits = [
        KnowledgeSearchHit(
            score=0.9,
            payload={"source_id": source_id, "project_id": "1", "text": "Устаревший секрет"},
        ),
        KnowledgeSearchHit(
            score=0.8,
            payload={"source_id": f"{kind}:999", "project_id": "1", "text": "Удалённый секрет"},
        ),
        KnowledgeSearchHit(
            score=0.7, payload={"source_id": source_id, "project_id": "2", "text": "Чужой секрет"}
        ),
    ]
    results = retrieve(
        catalog,
        fts_hits=[],
        semantic_hits=hits,
        query="источник",
        target_chars=2200,
        overlap_chars=300,
    )
    assert [item["source_id"] for item in results] == [source_id]
    assert "секрет" not in json.dumps(results, ensure_ascii=False)
    assert "Актуальный" in results[0]["text"]
    if kind is SourceType.ANALYTICS_REPORT:
        assert "Исходная предпосылка" in catalog.sources[source_id].text


def test_matching_tail_chunk_is_kept_and_reading_pages_covers_entire_source():
    rows = base_rows()
    rows["documents"][0]["content_md"] = (
        "Начальное требование. " * 1800 + "Важное решение в самом конце"
    )
    catalog = build_catalog(1, rows)
    chunks = catalog.sources["document:9"].chunks(2200, 300)
    hits = [
        KnowledgeSearchHit(score=0.9, payload=chunks[-1].payload),
        KnowledgeSearchHit(score=0.8, payload=chunks[-2].payload),
    ]
    result = retrieve(
        catalog,
        fts_hits=[{"source_id": "document:9"}],
        semantic_hits=hits,
        query="решение",
        target_chars=2200,
        overlap_chars=300,
    )[0]
    assert len(result["matched_locations"]) == 1 and "Важное решение" in result["text"]
    assert len(result["text"]) <= 400
    offset = 0
    pages = []
    while offset is not None:
        page = read_catalog(
            catalog,
            KnowledgeReadRequest(
                name="telos_read_source", source_id="document:9", offset=offset, max_chars=8000
            ),
        )
        pages.append(page["text"])
        offset = page["next_offset"]
    assert "".join(pages) == catalog.sources["document:9"].text
    assert "error" in read_catalog(
        catalog, KnowledgeReadRequest(name="telos_read_source", source_id="document:999")
    )


def test_relationship_and_source_lists_expose_next_page():
    rows = base_rows()
    rows["task_comments"] = [
        {"id": i, "task_id": 7, "author_name": "Автор", "body_md": str(i)} for i in range(35)
    ]
    catalog = build_catalog(1, rows)
    first = read_catalog(
        catalog, KnowledgeReadRequest(name="telos_list_sources", entity_type="comment", limit=30)
    )
    assert first["total"] == 35 and first["next_offset"] == 30
    second = read_catalog(
        catalog,
        KnowledgeReadRequest(
            name="telos_list_related_sources", source_id="task:7", entity_type="comment", offset=30
        ),
    )
    assert second["total"] == 35 and second["next_offset"] is None and len(second["items"]) == 5


def test_end_of_long_title_is_embedded_too():
    rows = base_rows()
    rows["tasks"][0]["title"] = "З" * 210 + " уникальный заголовок"
    chunks = build_catalog(1, rows).sources["task:7"].chunks(2200, 300)
    assert "уникальный заголовок" in chunks[0].text
