"""Sent-файлы строго в серверном контексте одного вопроса Арису."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import aliased

from src.db.models.agent_conversations import AgentConversation
from src.db.models.agent_files import AgentFile
from src.db.models.agent_messages import AgentMessage
from src.db.models.chat_agent_turns import ChatAgentTurn
from src.db.models.chat_attachments import ChatAttachment
from src.db.models.chat_file_texts import ChatFileText
from src.db.models.chat_messages import ChatMessage
from src.db.models.project_chats import ProjectChat
from src.db.models.projects import Project
from src.repositories.chat_base import ChatRepository


class AgentChatFilesRepository(ChatRepository):
    """Каждый публичный метод выполняет один SQL statement."""

    async def conversation_context(self, *, message_id: int, user_id: int) -> dict | None:
        """Выводит conversation и верхний вопрос из текущего ответа, а не аргументов модели."""
        question = aliased(AgentMessage)
        result = await self._execute(
            select(
                AgentConversation.id.label("scope_id"),
                AgentConversation.project_id,
                AgentConversation.channel,
                AgentConversation.api_key_id,
                question.id.label("upper_bound"),
                question.id.label("question_id"),
                Project.key.label("project_key"),
                AgentMessage.status,
                AgentMessage.run_id,
            )
            .select_from(AgentMessage)
            .join(AgentConversation, AgentConversation.id == AgentMessage.conversation_id)
            .outerjoin(Project, Project.id == AgentConversation.project_id)
            .join(
                question,
                and_(
                    question.conversation_id == AgentMessage.conversation_id,
                    question.request_id == AgentMessage.request_id,
                    question.role == "user",
                    question.status == "completed",
                ),
            )
            .where(
                AgentMessage.id == message_id,
                AgentMessage.role == "assistant",
                AgentConversation.user_id == user_id,
            )
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def shared_context(self, *, chat_turn_id: int, user_id: int) -> dict | None:
        """Верхняя граница общего чата — seq вопроса текущего хода."""
        reply = aliased(ChatMessage)
        result = await self._execute(
            select(
                ChatAgentTurn.chat_id.label("scope_id"),
                ChatAgentTurn.project_id,
                ChatMessage.seq.label("upper_bound"),
                ChatMessage.id.label("question_id"),
                Project.key.label("project_key"),
                ChatAgentTurn.status,
                ChatAgentTurn.run_id,
            )
            .select_from(ChatAgentTurn)
            .join(Project, Project.id == ChatAgentTurn.project_id)
            .join(
                ChatMessage,
                and_(
                    ChatMessage.id == ChatAgentTurn.request_message_id,
                    ChatMessage.chat_id == ChatAgentTurn.chat_id,
                    ChatMessage.author_user_id == user_id,
                    ChatMessage.deleted_at.is_(None),
                ),
            )
            .join(
                reply,
                and_(
                    reply.id == ChatAgentTurn.reply_message_id,
                    reply.chat_id == ChatAgentTurn.chat_id,
                    reply.deleted_at.is_(None),
                    reply.author_kind == "agent",
                ),
            )
            .where(ChatAgentTurn.id == chat_turn_id, ChatAgentTurn.requester_id == user_id)
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    @staticmethod
    def _files(*, kind: str, scope_id: int, upper_bound: int, q: str | None = None):
        if kind == "conversation":
            source, message = AgentFile, AgentMessage
            condition = and_(
                source.conversation_id == scope_id,
                message.conversation_id == source.conversation_id,
                message.role == "user",
                message.status == "completed",
                message.id <= upper_bound,
            )
            cache_key = ChatFileText.agent_file_id
            size = source.size
            order = message.id
        else:
            source, message = ChatAttachment, ChatMessage
            condition = and_(
                source.chat_id == scope_id,
                message.chat_id == source.chat_id,
                message.deleted_at.is_(None),
                message.seq <= upper_bound,
            )
            cache_key = ChatFileText.chat_attachment_id
            size = source.size_bytes
            order = message.seq
        statement = (
            select(
                source.id.label("file_id"),
                source.original_name,
                source.content_type,
                size.label("size"),
                source.message_id,
                source.created_at,
                source.storage_key,
                ChatFileText.status,
                ChatFileText.detail,
                ChatFileText.original_chars,
                ChatFileText.truncated,
                ChatFileText.content_hash,
                ChatFileText.total_chars,
                func.substr(ChatFileText.content, 1, 600).label("excerpt"),
            )
            .select_from(source)
            .join(message, source.message_id == message.id)
            .outerjoin(ChatFileText, cache_key == source.id)
            .where(condition)
        )
        if q:
            literal = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            statement = statement.where(source.original_name.ilike(f"%{literal}%", escape="\\"))
        return statement, source, order

    async def preparation_file(self, *, kind: str, file_id: UUID) -> dict | None:
        """Private lifecycle worker читает original snapshot, не раскрывая черновик агенту."""
        if kind == "conversation":
            source = AgentFile
            statement = (
                select(
                    source.id.label("file_id"),
                    source.original_name,
                    source.content_type,
                    source.size,
                    source.storage_key,
                    source.message_id,
                    source.created_at,
                    source.conversation_id.label("scope_id"),
                    AgentConversation.project_id,
                    AgentConversation.user_id.label("owner_user_id"),
                )
                .select_from(source)
                .join(AgentConversation, AgentConversation.id == source.conversation_id)
                .outerjoin(
                    AgentMessage,
                    and_(
                        AgentMessage.id == source.message_id,
                        AgentMessage.conversation_id == source.conversation_id,
                        AgentMessage.role == "user",
                    ),
                )
                .where(or_(source.message_id.is_(None), AgentMessage.id.is_not(None)))
            )
        else:
            source = ChatAttachment
            statement = (
                select(
                    source.id.label("file_id"),
                    source.original_name,
                    source.content_type,
                    source.size_bytes.label("size"),
                    source.storage_key,
                    source.message_id,
                    source.created_at,
                    source.chat_id.label("scope_id"),
                    ProjectChat.project_id,
                    source.uploader_id.label("owner_user_id"),
                )
                .select_from(source)
                .join(ProjectChat, ProjectChat.id == source.chat_id)
                .outerjoin(
                    ChatMessage,
                    and_(
                        ChatMessage.id == source.message_id,
                        ChatMessage.chat_id == source.chat_id,
                        ChatMessage.deleted_at.is_(None),
                    ),
                )
                .where(or_(source.message_id.is_(None), ChatMessage.id.is_not(None)))
            )
        row = (await self._execute(statement.where(source.id == file_id))).mappings().one_or_none()
        return dict(row) if row else None

    async def get_file(
        self, *, kind: str, scope_id: int, upper_bound: int, file_id: UUID
    ) -> dict | None:
        """Не находит черновики, будущие реплики и файлы другого диалога."""
        statement, source, _ = self._files(kind=kind, scope_id=scope_id, upper_bound=upper_bound)
        row = (await self._execute(statement.where(source.id == file_id))).mappings().one_or_none()
        return dict(row) if row else None

    async def count_files(
        self, *, kind: str, scope_id: int, upper_bound: int, q: str | None
    ) -> int:
        """Считает ту же доступную и отфильтрованную область, что и страница."""
        statement, _, _ = self._files(kind=kind, scope_id=scope_id, upper_bound=upper_bound, q=q)
        return (
            await self._execute(select(func.count()).select_from(statement.subquery()))
        ).scalar_one()

    async def list_files(
        self, *, kind: str, scope_id: int, upper_bound: int, q: str | None, limit: int, offset: int
    ) -> list[dict]:
        """Возвращает метаданные от текущего вопроса к более ранним файлам."""
        statement, source, order = self._files(
            kind=kind, scope_id=scope_id, upper_bound=upper_bound, q=q
        )
        rows = await self._execute(
            statement.order_by(order.desc(), source.created_at.desc(), source.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return [dict(row) for row in rows.mappings()]

    async def expired_conversation_drafts(self, *, cutoff, limit: int = 100) -> list[UUID]:
        """Список кандидатов TTL не является разрешением на удаление."""
        rows = await self._execute(
            select(AgentFile.id)
            .where(AgentFile.message_id.is_(None), AgentFile.created_at < cutoff)
            .order_by(AgentFile.created_at, AgentFile.id)
            .limit(limit)
        )
        return list(rows.scalars())

    async def existing_conversation_keys(self, *, keys: list[str]) -> set[str]:
        """Старые sent-файлы не становятся сиротами только из-за возраста."""
        rows = await self._execute(
            select(AgentFile.storage_key).where(AgentFile.storage_key.in_(keys))
        )
        return set(rows.scalars())

    async def delete_expired_conversation_draft(self, *, file_id: UUID, cutoff) -> dict | None:
        """Lock conversation → conditional DELETE совпадает с порядком обычного Send."""
        owner = select(AgentFile.conversation_id).where(AgentFile.id == file_id).scalar_subquery()
        locked = (
            select(AgentConversation.id)
            .where(AgentConversation.id == owner)
            .with_for_update()
            .cte("locked_conversation")
        )
        row = (
            (
                await self._execute(
                    delete(AgentFile)
                    .where(
                        AgentFile.id == file_id,
                        AgentFile.message_id.is_(None),
                        AgentFile.created_at < cutoff,
                        AgentFile.conversation_id.in_(select(locked.c.id)),
                    )
                    .returning(AgentFile.storage_key)
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None
