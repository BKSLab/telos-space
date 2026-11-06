"""Однократное описание версии текста вне области соединения с БД."""

import json

from pydantic import BaseModel, Field

from src.clients.llm import LlmClient
from src.knowledge.catalog import ProjectSource, SourceType
from src.prompts.source_summary import SOURCE_SUMMARY_PROMPT


class SourceSummaryOutput(BaseModel):
    summary: str = Field(min_length=1, max_length=800)


class SourceSummariesService:
    def __init__(self, *, llm_client: LlmClient, chunk_chars: int):
        self.llm_client = llm_client
        self.chunk_chars = chunk_chars

    async def summarize(self, source: ProjectSource) -> dict:
        """Большой файл читается частями целиком; короткий не требует вызова LLM.

        Args:
            source: Проверенный источник текущей версии проекта.
        Returns:
            Описание до 800 символов и идентификаторы версии для сохранения.
        Raises:
            ClientError: Внешняя модель недоступна или вернула неверный JSON.
            ValueError: Модель вернула пустое описание.
        """
        summary = source.text.strip()
        if len(summary) > 800:
            summary = ""
            # Срезы сохраняют заголовки и даже очень длинные строки без пробелов;
            # ни один вызов не превышает установленный размер части.
            chunks = [
                source.text[start : start + self.chunk_chars]
                for start in range(0, len(source.text), self.chunk_chars)
            ]
            for index, chunk in enumerate(chunks):
                result = await self.llm_client.get_structured_response(
                    system_prompt=SOURCE_SUMMARY_PROMPT,
                    content=json.dumps(
                        {
                            "title": source.title,
                            "previous_summary": summary,
                            "part": index + 1,
                            "parts": len(chunks),
                            "text": chunk,
                        },
                        ensure_ascii=False,
                    ),
                    schema=SourceSummaryOutput,
                    max_completion_tokens=700,
                )
                summary = result.summary.replace("\x00", "").strip()
                if not summary:
                    raise ValueError("Модель вернула пустое описание источника.")
        return {
            "project_id": source.project_id,
            "source_id": source.source_id,
            "document_id": source.entity_id if source.kind is SourceType.DOCUMENT else None,
            "attachment_id": source.entity_id if source.kind is SourceType.ATTACHMENT else None,
            "project_file_id": source.entity_id if source.kind is SourceType.PROJECT_FILE else None,
            "content_hash": source.summary_hash,
            "summary": summary,
        }
