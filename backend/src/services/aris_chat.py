"""Арис в общем чате проекта: очередь ходов и ответ обычным сообщением чата.

Сообщение участника, адресованное Арису, сохраняет ``ProjectChatService.send``
вместе с пустым ответом Ариса и строкой хода (одна транзакция). Этот сервис
забирает ход из постоянной очереди, собирает контекст (проект, последние
сообщения чата, цитируемое сообщение), вызывает модель с правами позвавшего и
записывает ответ в то же сообщение. Все изменения видны команде событием
``message.updated`` через обычный outbox чата.

Порядок блокировок везде один: строка хода, затем строка чата. Его же держат
решение по действию и выполнение инструмента, поэтому циклов ожидания нет.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from src.agent.aris import ARIS_NAME, ArisMode, strip_address
from src.agent.progress import ProgressSink
from src.agent.tools import AgentToolContext
from src.db.models.chat_agent_turns import ChatAgentTurn
from src.exceptions.access import AccessServiceError, ResourceNotAvailableError
from src.exceptions.ai import AiProviderError
from src.exceptions.base import RepositoryError
from src.schemas.agent_tools import AgentToolRunSchema
from src.schemas.knowledge import KnowledgeAnswerSchema, KnowledgeChatMessageSchema
from src.services.db_scope import ArisChatScope, ArisChatScopeFactory
from src.services.project_agent import ProjectAgentService
from src.services.tasks import user_display_name

logger = logging.getLogger(__name__)

HISTORY_MESSAGE_CHARS = 2000


class ArisChatUnavailableError(Exception):
    """Ход больше нельзя выполнять: позвавший ушёл из проекта или сообщение удалено."""


@dataclass(frozen=True, slots=True)
class ArisChatConfig:
    """Бюджеты ответа Ариса в чате."""

    turn_timeout_seconds: float
    history_messages: int = 30


class ArisChatService:
    """Исполняет ходы Ариса в чатах проектов; БД не удерживается во время вызова модели."""

    def __init__(
        self, *, scope: ArisChatScopeFactory, agent: ProjectAgentService, config: ArisChatConfig
    ) -> None:
        self.scope = scope
        self.agent = agent
        self.config = config

    async def process_next(self) -> bool:
        """Выполняет один ход или завершает ошибкой одну прерванную попытку.

        Returns:
            True, если очередь содержала задание.
        """
        async with self._scope() as db:
            expired = await db.turns.claim_expired(self.config.turn_timeout_seconds)
            if expired is not None:
                await self._write_reply(
                    db,
                    expired,
                    content=self._error_text("подготовка ответа прервалась"),
                    sources=[],
                )
                await db.unit_of_work.commit()
                logger.warning("⚠️ Ход Ариса id=%s завершён по таймауту.", expired.id)
                return True
            turn = await db.turns.claim_next(uuid4())
            await db.unit_of_work.commit()
        if turn is None:
            return False
        logger.info("🤖 Арис отвечает в чате проекта id=%s, ход=%s.", turn.project_id, turn.id)
        try:
            async with asyncio.timeout(self.config.turn_timeout_seconds):
                answer = await self._generate(turn)
            await self._finish(turn, answer=answer)
            logger.info("✅ Ответ Ариса в чате, ход=%s.", turn.id)
        except asyncio.CancelledError:
            try:
                await self._finish(turn, error="подготовка ответа прервалась")
            except Exception:
                logger.error(
                    "❌ Не удалось сохранить остановку хода id=%s.", turn.id, exc_info=True
                )
            raise
        except TimeoutError:
            await self._finish(turn, error="не уложился во время ответа")
        except ArisChatUnavailableError as error:
            await self._finish(turn, error=str(error))
        except AiProviderError:
            logger.error("❌ Модель не ответила на ход id=%s.", turn.id, exc_info=True)
            await self._finish(turn, error="модель сейчас недоступна")
        except Exception:
            # Граница фонового задания: любая ошибка становится видимым сообщением
            # в чате, а не вечным «Арис печатает».
            logger.error("❌ Ход Ариса id=%s не выполнен.", turn.id, exc_info=True)
            await self._finish(turn, error="внутренняя ошибка")
        return True

    async def _generate(self, turn: ChatAgentTurn) -> KnowledgeAnswerSchema:
        async with self._scope() as db:
            request = await db.messages.get(turn.chat_id, turn.request_message_id)
            if request is None or request.deleted_at is not None:
                raise ArisChatUnavailableError("вопрос удалён")
            if await db.participants.get_member(turn.project_id, turn.requester_id) is None:
                raise ArisChatUnavailableError("позвавший больше не участник проекта")
            try:
                # Роль пересчитывается на каждый ответ: VIEWER получает только чтение.
                grant = await db.access.ensure_project_access(
                    project_id=turn.project_id, user_id=turn.requester_id
                )
            except ResourceNotAvailableError as error:
                raise ArisChatUnavailableError("проект больше недоступен") from error
            user = await db.users.get_by_id(turn.requester_id)
            if user is None or not user.is_active:
                raise ArisChatUnavailableError("позвавший больше не активен")
            earlier = await db.messages.get_page(
                turn.chat_id, before=request.seq, limit=self.config.history_messages
            )
            quoted = (
                await db.messages.get(turn.chat_id, request.reply_to_message_id)
                if request.reply_to_message_id
                else None
            )
            author_ids = {
                row.author_user_id for row in [*earlier, *([quoted] if quoted else [])]
            } - {None}
            names = {
                row["id"]: " ".join(filter(None, (row["last_name"], row["first_name"]))) or row["username"]
                for row in await db.participants.get_users(list(author_ids))
            }
            action_history = [
                AgentToolRunSchema.model_validate(item).model_dump(mode="json")
                for item in await db.runs.get_for_turns([turn.id])
            ]
        actor = {"user_id": user.id, "username": user.username, "name": user_display_name(user)}
        history = [
            item
            for item in (self._history(row, names) for row in reversed(earlier))
            if item is not None
        ]
        extra: dict[str, Any] = {}
        if quoted is not None and quoted.deleted_at is None and quoted.content.strip():
            extra["quoted_message"] = {
                "author": self._author(quoted, names),
                "content": quoted.content[:HISTORY_MESSAGE_CHARS],
            }
        return await self.agent.ask(
            project_id=turn.project_id,
            question=strip_address(request.content),
            history=history,
            actor=actor,
            execution=AgentToolContext(
                project_id=turn.project_id,
                user_id=turn.requester_id,
                can_write=grant.can_write,
                chat_turn_id=turn.id,
                run_id=turn.run_id,
            ),
            action_history=action_history,
            mode=ArisMode.PROJECT_CHAT,
            extra_context=extra,
            conversation_id=f"chat:{turn.chat_id}",
            on_progress=self._progress_sink(turn),
        )

    def _progress_sink(self, turn: ChatAgentTurn) -> ProgressSink:
        """Живой статус хода: строка хода и message.updated всей команде.

        Порядок блокировок тот же, что везде: строка хода, затем строка чата
        (``chat_emit``). Завершённый ход статус не получает.
        """

        async def write(progress: str) -> None:
            async with self.scope() as db:
                if await db.turns.set_progress(turn.id, turn.run_id, progress) is None:
                    return
                await db.turns.emit(turn.project_id, "message.updated", turn.reply_message_id)
                await db.unit_of_work.commit()

        return write

    async def _finish(
        self,
        turn: ChatAgentTurn,
        *,
        answer: KnowledgeAnswerSchema | None = None,
        error: str | None = None,
    ) -> None:
        sources = [] if answer is None else [s.model_dump(mode="json") for s in answer.sources]
        data = (
            {"status": "failed", "error": error}
            if error is not None
            else {"status": "completed", "error": None, "sources": sources}
        )
        async with self._scope() as db:
            # Только своя активная попытка: истёкшую уже закрыл другой воркер.
            if not await db.turns.finish(turn.id, turn.run_id, data):
                return
            content = (
                self._error_text(error)
                if error is not None
                else answer.answer.replace("\x00", "").strip()
            )
            await self._write_reply(db, turn, content=content, sources=sources)
            await db.unit_of_work.commit()

    @staticmethod
    async def _write_reply(
        db: ArisChatScope, turn: ChatAgentTurn, *, content: str, sources: list
    ) -> None:
        """Меняет ответ-заглушку на итоговый текст и публикует message.updated."""
        chat = await db.chats.get(turn.project_id, lock=True)
        if chat is None:
            return
        reply = await db.messages.get(turn.chat_id, turn.reply_message_id)
        if reply is None:
            return
        if not content and not sources:
            content = "Готово."
        await db.messages.update(
            reply.id,
            dict(content=content, revision=reply.revision + 1),
        )
        seq = await db.chats.next_seq(chat.id)
        await db.events.save(chat.id, seq, "message.updated", {"message_id": reply.id})

    @staticmethod
    def _error_text(reason: str) -> str:
        return f"Не получилось ответить: {reason}. Спросите ещё раз."

    @staticmethod
    def _author(row, names: dict[int, str]) -> str:
        if row.author_kind == "agent":
            return ARIS_NAME
        return names.get(row.author_user_id, "Участник")

    @classmethod
    def _history(cls, row, names: dict[int, str]) -> KnowledgeChatMessageSchema | None:
        if row.deleted_at is not None or not row.content.strip():
            return None
        content = row.content
        if len(content) > HISTORY_MESSAGE_CHARS:
            content = content[: HISTORY_MESSAGE_CHARS - 40] + "\n[сообщение сокращено]"
        return KnowledgeChatMessageSchema(
            role="assistant" if row.author_kind == "agent" else "user",
            content=f"{cls._author(row, names)}: {content}",
        )

    @asynccontextmanager
    async def _scope(self) -> AsyncIterator[ArisChatScope]:
        try:
            async with self.scope() as db:
                yield db
        except (RepositoryError, AccessServiceError):
            logger.error("❌ Хранилище ходов Ариса недоступно.", exc_info=True)
            raise
