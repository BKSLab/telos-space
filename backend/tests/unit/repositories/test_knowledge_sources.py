"""Сервисные внешние источники не открывают raw SQL обход общего reader."""

from unittest.mock import AsyncMock

from src.knowledge.catalog import SourceType
from src.repositories.knowledge_sources import KnowledgeSourcesRepository


async def test_git_source_filter_has_no_unsafe_generic_fts_branch() -> None:
    session = AsyncMock()
    result = await KnowledgeSourcesRepository(session).search(
        7, "private repository content", entity_type=SourceType.GIT_FILE,
    )
    assert result == []
    session.execute.assert_not_awaited()
