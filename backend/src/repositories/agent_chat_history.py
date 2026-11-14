"""История одной проверенной переписки, с SQL-фильтрами до LIMIT."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy import and_, case, func, literal, or_, select

from src.db.models.agent_conversations import AgentConversation
from src.db.models.agent_files import AgentFile
from src.db.models.agent_messages import AgentMessage
from src.db.models.chat_agent_turns import ChatAgentTurn
from src.db.models.chat_attachments import ChatAttachment
from src.db.models.chat_file_texts import ChatFileText
from src.db.models.chat_message_entities import ChatMessageEntity
from src.db.models.chat_messages import ChatMessage
from src.db.models.users import User
from src.repositories.chat_base import ChatRepository


class AgentChatHistoryRepository(ChatRepository):
    """Один SQL statement на публичный метод; ACL определяет общий сервис файлов."""

    @staticmethod
    def _messages(*, kind: str, scope_id: int, upper_bound: int):
        name = func.coalesce(
            func.nullif(
                func.trim(func.concat_ws(" ", User.last_name, User.first_name, User.middle_name)),
                "",
            ),
            User.username,
            literal("Удалённый пользователь"),
        )
        if kind == "conversation":
            source = AgentMessage
            order = source.id
            is_user = source.role == "user"
            author = case((is_user, AgentConversation.user_id), else_=None)
            statement = (
                select(
                    source.id,
                    order.label("ordinal"),
                    source.content,
                    source.created_at,
                    source.sources.label("entity_sources"),
                    literal(1).label("revision"),
                    literal(None).label("reply_to_message_id"),
                    author.label("author_id"),
                    case((is_user, name), else_="Арис").label("author_name"),
                    case((is_user, "USER"), else_="AGENT").label("author_type"),
                )
                .select_from(source)
                .join(AgentConversation, AgentConversation.id == source.conversation_id)
                .outerjoin(User, User.id == AgentConversation.user_id)
                .where(
                    source.conversation_id == scope_id,
                    source.id <= upper_bound,
                    source.status == "completed",
                )
            )
            vector = func.to_tsvector("russian", func.coalesce(source.content, "")).op("||")(
                func.to_tsvector("simple", func.coalesce(source.content, ""))
            )
        else:
            source = ChatMessage
            order = source.seq
            is_user = source.author_kind == "user"
            author = source.author_user_id
            statement = (
                select(
                    source.id,
                    order.label("ordinal"),
                    source.content,
                    source.created_at,
                    ChatAgentTurn.sources.label("entity_sources"),
                    source.revision,
                    source.reply_to_message_id,
                    author.label("author_id"),
                    case((is_user, name), else_="Арис").label("author_name"),
                    case((is_user, "USER"), else_="AGENT").label("author_type"),
                )
                .select_from(source)
                .outerjoin(User, User.id == source.author_user_id)
                .outerjoin(ChatAgentTurn, ChatAgentTurn.reply_message_id == source.id)
                .where(
                    source.chat_id == scope_id,
                    source.seq <= upper_bound,
                    source.deleted_at.is_(None),
                    or_(is_user, ChatAgentTurn.status == "completed"),
                )
            )
            vector = source.search_vector
        return statement, source, order, author, vector

    async def get_message(
        self, *, kind: str, scope_id: int, upper_bound: int, message_id: int
    ) -> dict | None:
        """Сообщение другого чата, будущий/удалённый ответ и незавершённый Арис не видны."""
        statement, source, _, _, _ = self._messages(
            kind=kind, scope_id=scope_id, upper_bound=upper_bound
        )
        row = (
            (await self._execute(statement.where(source.id == message_id))).mappings().one_or_none()
        )
        return dict(row) if row else None

    async def list_messages(
        self,
        *,
        kind: str,
        scope_id: int,
        upper_bound: int,
        limit: int,
        before_order: int | None = None,
        after_order: int | None = None,
        query: str | None = None,
        author_user_id: int | None = None,
        author_type: Literal["USER", "AGENT"] | None = None,
        start_at: datetime | None = None,
        end_before: datetime | None = None,
    ) -> list[dict]:
        """Все ограничения применяются до сортировки и LIMIT, включая живые ответы Ариса."""
        statement, source, order, author, vector = self._messages(
            kind=kind, scope_id=scope_id, upper_bound=upper_bound
        )
        if before_order is not None:
            statement = statement.where(order < before_order)
        if after_order is not None:
            statement = statement.where(order > after_order)
        if query:
            attachment = AgentFile if kind == "conversation" else ChatAttachment
            attachment_scope = (
                attachment.conversation_id == scope_id
                if kind == "conversation"
                else attachment.chat_id == scope_id
            )
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            filename_match = (
                select(literal(1))
                .select_from(attachment)
                .where(
                    attachment.message_id == source.id,
                    attachment_scope,
                    attachment.original_name.ilike(f"%{escaped}%", escape="\\"),
                )
                .correlate(source)
                .exists()
            )
            if kind == "conversation":
                filename_match = and_(source.role == "user", filename_match)
            statement = statement.where(
                or_(
                    vector.op("@@")(func.websearch_to_tsquery("russian", query)),
                    vector.op("@@")(func.websearch_to_tsquery("simple", query)),
                    filename_match,
                )
            )
        if author_user_id is not None:
            statement = statement.where(author == author_user_id)
        if author_type is not None:
            if kind == "conversation":
                statement = statement.where(
                    source.role == ("user" if author_type == "USER" else "assistant")
                )
            else:
                statement = statement.where(
                    source.author_kind == ("user" if author_type == "USER" else "agent")
                )
        if start_at is not None:
            statement = statement.where(source.created_at >= start_at)
        if end_before is not None:
            statement = statement.where(source.created_at < end_before)
        rows = await self._execute(
            statement.order_by(order.asc() if after_order is not None else order.desc()).limit(
                limit
            )
        )
        return [dict(row) for row in rows.mappings()]

    async def entity_links(
        self, *, scope_id: int, upper_bound: int, message_ids: list[int]
    ) -> list[dict]:
        """Пакетно читает только identity-связи живых сообщений этого общего чата."""
        rows = await self._execute(
            select(
                ChatMessageEntity.message_id,
                ChatMessageEntity.entity_type,
                ChatMessageEntity.entity_id,
                ChatMessageEntity.document_ref,
            )
            .join(ChatMessage, ChatMessage.id == ChatMessageEntity.message_id)
            .where(
                ChatMessage.chat_id == scope_id,
                ChatMessage.seq <= upper_bound,
                ChatMessage.deleted_at.is_(None),
                ChatMessage.id.in_(message_ids),
            )
            .order_by(ChatMessageEntity.position, ChatMessageEntity.id)
        )
        return [dict(row) for row in rows.mappings()]

    async def attachments(
        self, *, kind: str, scope_id: int, upper_bound: int, message_ids: list[int]
    ) -> list[dict]:
        """Только вложенные sent-файлы; читается bounded prefix готового кэша без enqueue/OCR."""
        if kind == "conversation":
            source, message = AgentFile, AgentMessage
            cache_key = ChatFileText.agent_file_id
            size = source.size
            condition = and_(
                source.conversation_id == scope_id,
                message.conversation_id == source.conversation_id,
                message.role == "user",
                message.status == "completed",
                message.id <= upper_bound,
            )
        else:
            source, message = ChatAttachment, ChatMessage
            cache_key = ChatFileText.chat_attachment_id
            size = source.size_bytes
            condition = and_(
                source.chat_id == scope_id,
                message.chat_id == source.chat_id,
                message.deleted_at.is_(None),
                message.seq <= upper_bound,
            )
        rows = await self._execute(
            select(
                source.id.label("file_id"),
                source.original_name,
                source.content_type,
                size.label("size"),
                source.message_id,
                source.created_at,
                func.coalesce(ChatFileText.status, "pending").label("status"),
                ChatFileText.detail,
                func.coalesce(ChatFileText.original_chars, 0).label("original_chars"),
                func.coalesce(ChatFileText.total_chars, 0).label("total_chars"),
                func.coalesce(ChatFileText.truncated, False).label("truncated"),
                ChatFileText.content_hash,
                case(
                    (ChatFileText.status == "ready", func.substr(ChatFileText.content, 1, 2000)),
                    else_=None,
                ).label("excerpt"),
            )
            .select_from(source)
            .join(message, source.message_id == message.id)
            .outerjoin(ChatFileText, cache_key == source.id)
            .where(condition, source.message_id.in_(message_ids))
            .order_by(source.created_at, source.id)
        )
        return [dict(row) for row in rows.mappings()]
