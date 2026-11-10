"""Единое чтение каталога и проверка поисковых кандидатов по текущему SQL-срезу."""

import re
from collections import defaultdict
from typing import Any

from src.clients.qdrant import KnowledgeSearchHit
from src.knowledge.catalog import ProjectCatalog, ProjectSource, SourceType
from src.knowledge.retrieval import reciprocal_rank_fusion
from src.schemas.knowledge import KnowledgeReadRequest, KnowledgeSourceSchema


def _bounded(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= 2000 else value[:2000] + "… [полностью: telos_read_source]"
    if isinstance(value, dict):
        return {key: _bounded(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_bounded(item) for item in value[:40]]
    return value


def source_view(source: ProjectSource, *, offset: int = 0, max_chars: int = 1200) -> dict:
    payload = source.payload()
    end = min(len(source.text), offset + max_chars)
    return {
        "source_id": source.source_id,
        "entity_type": source.kind.value,
        "entity_id": source.entity_id,
        "title": source.title,
        "properties": _bounded(payload["properties"]),
        "provenance": payload["provenance"],
        "parent_source_id": source.parent_source_id,
        "relations": source.relations[:30],
        "relations_total": len(source.relations),
        "text": source.text[offset:end],
        "offset": offset,
        "total_chars": len(source.text),
        "next_offset": end if end < len(source.text) else None,
    }


def source_brief(source: ProjectSource, *, offset: int = 0) -> dict:
    """Описание для отбора: без тела документа, графа связей и тяжёлых свойств."""
    payload = source.payload()
    fields = {
        "task_key",
        "slug",
        "status",
        "stage_id",
        "priority",
        "assignee",
        "role",
        "start_date",
        "due_date",
        "is_done_stage",
        "risk_level",
        "owner_user_id",
        "original_name",
        "content_type",
        "task_id",
        "user_id",
        "updated_at",
    }
    properties = {
        key: value
        for key, value in payload["properties"].items()
        if key in fields and value is not None
    }
    is_document = source.kind in {
        SourceType.DOCUMENT,
        SourceType.ATTACHMENT,
        SourceType.PROJECT_FILE,
    }
    result = {
        "source_id": source.source_id,
        "entity_type": source.kind.value,
        "entity_id": source.entity_id,
        "title": source.title[:250],
        "properties": properties,
        "text": source.summary
        if is_document and source.summary
        else source.text[offset : offset + 400],
        "content_kind": "summary" if is_document and source.summary else "preview",
        "total_chars": len(source.text),
        "relations_total": len(source.relations),
    }
    if is_document:
        result["summary_status"] = "ready" if source.summary else "pending"
    if source.kind in {SourceType.ATTACHMENT, SourceType.PROJECT_FILE}:
        extraction = source.properties.get("extraction", {})
        result["extraction_status"] = extraction.get("status", "pending")
        if extraction.get("detail"):
            result["extraction_detail"] = extraction["detail"][:200]
    if offset:
        result["matched_locations"] = [{"offset": offset}]
    if not source.summary:
        result["offset"] = offset
        result["next_offset"] = offset + 400 if offset + 400 < len(source.text) else None
    return result


def citation(
    source: ProjectSource, *, excerpt: str, score: float | None = None
) -> KnowledgeSourceSchema:
    payload = source.payload()
    return KnowledgeSourceSchema(
        source_id=source.source_id,
        entity_type=source.kind,
        entity_id=source.entity_id,
        title=source.title,
        excerpt=excerpt[:500],
        score=score,
        task_id=int(payload["task_id"]) if payload["task_id"] else None,
        document_slug=payload["document_slug"],
        related_source_ids=payload["related_source_ids"][:30],
    )


def read_catalog(
    catalog: ProjectCatalog, request: KnowledgeReadRequest, *, search_hits: list[dict] | None = None
) -> dict:
    """Возвращает явные границы страниц; неизвестные/чужие ID не разрешаются."""
    if request.name in {"telos_read_source", "telos_list_related_sources"}:
        source = catalog.sources.get(request.source_id or "")
        if source is None:
            return {"error": "Источник не найден в этом проекте."}
        if request.name == "telos_read_source":
            return source_view(source, offset=request.offset, max_chars=request.max_chars)
        links = [
            link
            for link in source.relations
            if request.entity_type is None
            or catalog.sources[link["source_id"]].kind == request.entity_type
        ]
        page = links[request.offset : request.offset + request.limit]
        return {
            "items": [
                {
                    "relation": link,
                    "source": source_brief(catalog.sources[link["source_id"]]),
                }
                for link in page
            ],
            "offset": request.offset,
            "total": len(links),
            "next_offset": request.offset + len(page)
            if request.offset + len(page) < len(links)
            else None,
        }
    if request.name == "telos_search_sources":
        page = (search_hits or [])[: request.limit]
        return {
            "items": [
                source_brief(
                    catalog.sources[hit["source_id"]],
                    offset=matching_offset(
                        catalog.sources[hit["source_id"]].text, request.query or ""
                    ),
                )
                for hit in page
                if hit["source_id"] in catalog.sources
            ],
            "offset": request.offset,
            "next_offset": request.offset + request.limit
            if len(search_hits or []) > request.limit
            else None,
        }
    sources = sorted(
        (
            source
            for source in catalog.sources.values()
            if request.entity_type is None or source.kind == request.entity_type
        ),
        key=lambda source: (source.kind.value, source.entity_id),
    )
    page = sources[request.offset : request.offset + request.limit]
    return {
        "items": [source_brief(source) for source in page],
        "offset": request.offset,
        "total": len(sources),
        "next_offset": request.offset + len(page)
        if request.offset + len(page) < len(sources)
        else None,
    }


def matching_offset(text: str, query: str) -> int:
    """Находит актуальный фрагмент для FTS/устаревшего векторного кандидата."""
    lowered = text.casefold()
    positions = [lowered.find(word) for word in re.findall(r"[\w-]{3,}", query.casefold())]
    found = [pos for pos in positions if pos >= 0]
    return max(0, min(found) - 200) if found else 0


def retrieve(
    catalog: ProjectCatalog,
    *,
    fts_hits: list[dict],
    semantic_hits: list[KnowledgeSearchHit],
    query: str,
    target_chars: int,
    overlap_chars: int,
    limit: int = 5,
) -> list[dict]:
    """Qdrant задаёт кандидатов; весь текст и метаданные читаются из текущего каталога."""
    by_source = defaultdict(list)
    for hit in semantic_hits:
        source_id = hit.payload.get("source_id")
        if (
            str(hit.payload.get("project_id")) == str(catalog.project_id)
            and source_id in catalog.sources
        ):
            by_source[source_id].append(hit)
    scores = reciprocal_rank_fusion(
        [
            [hit["source_id"] for hit in fts_hits if hit["source_id"] in catalog.sources],
            list(by_source),
        ]
    )
    result = []
    for source_id, score in list(scores.items())[:limit]:
        source = catalog.sources[source_id]
        item = source_brief(source, offset=matching_offset(source.text, query))
        item["score"] = score
        chunks = None
        verified = []
        for hit in by_source[source_id][:1]:
            index = hit.payload.get("chunk_index")
            if not isinstance(index, int) or index < 0:
                continue
            if chunks is None:
                chunks = source.chunks(target_chars, overlap_chars)
            if index < len(chunks) and chunks[index].payload["text_hash"] == hit.payload.get(
                "text_hash"
            ):
                text = chunks[index].text
                offset = source.text.find(text)
                if offset < 0:
                    # Markdown chunks нормализуют пробелы; offset всегда относится
                    # к оригиналу, а не к нормализованной строке или номеру chunk.
                    offset = matching_offset(source.text, query)
                verified.append({"chunk_index": index, "offset": max(0, offset)})
                # Пока описание не готово, показываем один короткий актуальный
                # фрагмент. Кандидат из Qdrant никогда не отдаёт свой старый текст.
                if not source.summary:
                    start = matching_offset(text, query)
                    item["offset"] = (
                        max(0, offset) + start if source.text.find(text) >= 0 else offset
                    )
                    item["text"] = source.text[item["offset"] : item["offset"] + 400]
                    end = item["offset"] + len(item["text"])
                    item["next_offset"] = end if end < len(source.text) else None
        if verified:
            item["matched_locations"] = verified
        result.append(item)
    return result


def file_issues(catalog: ProjectCatalog) -> list[dict]:
    return [
        {
            "source_id": source.source_id,
            "title": source.title,
            "status": source.properties["extraction"]["status"],
            "detail": source.properties["extraction"].get("detail"),
        }
        for source in catalog.sources.values()
        if source.kind in {SourceType.ATTACHMENT, SourceType.PROJECT_FILE}
        and source.properties["extraction"]["status"] not in {"ready", "document"}
    ]


def coverage(
    catalog: ProjectCatalog,
    manifest: dict,
    *,
    target_chars: int,
    overlap_chars: int,
    embedding_signature: str | None = None,
) -> tuple[list[dict], int]:
    expected_ids = set()
    counts = {
        kind.value: {"entity_type": kind.value, "total": 0, "indexed": 0, "missing": 0, "stale": 0}
        for kind in SourceType
    }
    for source in catalog.sources.values():
        row = counts[source.kind.value]
        row["total"] += 1
        chunks = source.chunks(target_chars, overlap_chars)
        expected_ids.update(chunk.point_id for chunk in chunks)
        if any(chunk.point_id not in manifest for chunk in chunks):
            row["missing"] += 1
        elif any(
            {
                key: value
                for key, value in manifest[chunk.point_id].items()
                if key != "embedding_signature"
            }
            != chunk.payload
            or (
                embedding_signature is not None
                and manifest[chunk.point_id].get("embedding_signature") != embedding_signature
            )
            for chunk in chunks
        ):
            row["stale"] += 1
        else:
            row["indexed"] += 1
    return list(counts.values()), len(set(manifest) - expected_ids)
