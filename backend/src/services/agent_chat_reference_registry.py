"""Непрозрачные ссылки текущего запуска на серверно прочитанные сообщения."""

from uuid import uuid4

from src.agent.tools import AgentToolContext
from src.exceptions.ai import AgentAnswerError
from src.exceptions.base import ServiceError
from src.schemas.chat_references import ChatMessageReferenceSchema
from src.services.agent_chat_file_actions import file_context
from src.services.agent_chat_history import AgentChatHistoryService


class ChatReferenceRegistry:
    """Хранит неизменяемые цитаты вне модели и не смешивает их с Qdrant-источниками."""

    def __init__(self) -> None:
        self.nonce = uuid4().hex[:12]
        self.records: dict[str, ChatMessageReferenceSchema] = {}

    def register(self, value) -> None:
        """Регистрирует только DTO из history reader, не парсит текст переписки."""
        if isinstance(value, dict):
            reference = value.get("reference")
            if reference is not None and value.get("kind") in {"project_chat", "conversation"}:
                record = ChatMessageReferenceSchema.model_validate(reference)
                if (record.message_id != value.get("id") or record.chat_id != value.get("chat_id")
                        or record.kind != value["kind"] or record.state != "available"):
                    raise AgentAnswerError("Источник сообщения не совпадает с прочитанной репликой.")
                if record.quote:
                    handle = next((key for key, previous in self.records.items() if previous == record), None)
                    if handle is None:
                        handle = f"MSG_{self.nonce}_{len(self.records) + 1}"
                        self.records[handle] = record.model_copy(deep=True)
                    value["message_reference_handle"] = handle
                    value["citation_range"] = {"start": record.start, "end": record.end}
                    value["content_hash"] = record.content_hash
                # Текст цитаты уже в content; полный серверный снимок не дублируется в prompt.
                value.pop("reference", None)
            for key, child in list(value.items()):
                if key not in {"content", "excerpt", "quote"}:
                    self.register(child)
        elif isinstance(value, list):
            for child in value:
                self.register(child)

    async def select(
        self, *, handles: list[str], history: AgentChatHistoryService | None, context: AgentToolContext,
    ) -> list[ChatMessageReferenceSchema]:
        """Перечитывает выбранные источники с актуальным доступом перед публикацией."""
        selected = []
        seen = set()
        for handle in handles:
            if handle in seen:
                continue
            record = self.records.get(handle)
            if record is None or history is None:
                raise AgentAnswerError("Цитата не принадлежит прочитанным сообщениям этого ответа.")
            try:
                selected.append(await history.verify_reference(context=file_context(context), reference=record))
            except ServiceError as error:
                raise AgentAnswerError("Сообщение или доступ изменились. Повторите вопрос.") from error
            seen.add(handle)
        return selected
