"""Кэш извлечения с одним конкурентным владельцем на оригинал."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import and_, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from src.db.models.agent_files import AgentFile
from src.db.models.chat_attachments import ChatAttachment
from src.db.models.chat_file_texts import ChatFileText
from src.repositories.chat_base import ChatRepository


class ChatFileTextsRepository(ChatRepository):
    """Каждый метод — один statement; оригиналы и транзакции принадлежат сервису."""

    @staticmethod
    def _key(kind: str):
        return (
            ChatFileText.agent_file_id
            if kind == "conversation"
            else ChatFileText.chat_attachment_id
        )

    async def get(self, *, kind: str, file_id: UUID) -> dict | None:
        """Читает готовый результат или состояние единственного lease."""
        row = (
            (await self._execute(select(ChatFileText.__table__).where(self._key(kind) == file_id)))
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None

    async def enqueue(self, *, kind: str, file_ids: list[UUID]) -> None:
        """Ставит legacy-файлы в очередь без чтения оригинала и без повторного OCR."""
        if not file_ids:
            return
        source = AgentFile if kind == "conversation" else ChatAttachment
        await self._execute(
            insert(ChatFileText)
            .from_select(
                [self._key(kind).key, "status"],
                select(source.id, literal("pending")).where(source.id.in_(file_ids)),
            )
            .on_conflict_do_nothing(index_elements=[self._key(kind)])
        )

    async def claim_next(self, *, token: UUID, lease_seconds: int) -> dict | None:
        """Захватывает одну подготовку, не занимая очередь ответов Ариса."""
        candidate = (
            select(ChatFileText.id)
            .where(
                or_(
                    ChatFileText.status == "pending",
                    and_(
                        ChatFileText.status == "processing", ChatFileText.lease_until <= func.now()
                    ),
                )
            )
            .order_by(ChatFileText.updated_at, ChatFileText.id)
            .limit(1)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        row = (
            (
                await self._execute(
                    update(ChatFileText)
                    .where(ChatFileText.id == candidate)
                    .values(
                        status="processing",
                        detail=None,
                        lease_token=token,
                        lease_until=func.now() + timedelta(seconds=lease_seconds),
                        updated_at=func.now(),
                    )
                    .returning(*ChatFileText.__table__.c)
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None

    async def renew(self, *, kind: str, file_id: UUID, token: UUID, lease_seconds: int) -> bool:
        """Продлевает живой lease отдельной короткой транзакцией."""
        row = await self._execute(
            update(ChatFileText)
            .where(
                self._key(kind) == file_id,
                ChatFileText.status == "processing",
                ChatFileText.lease_token == token,
                ChatFileText.lease_until > func.now(),
            )
            .values(
                lease_until=func.now() + timedelta(seconds=lease_seconds), updated_at=func.now()
            )
            .returning(ChatFileText.id)
        )
        return row.scalar_one_or_none() is not None

    async def finish(self, *, kind: str, file_id: UUID, token: UUID, values: dict) -> dict | None:
        """Истёкший или перехваченный lease не может перезаписать новый результат."""
        row = (
            (
                await self._execute(
                    update(ChatFileText)
                    .where(
                        self._key(kind) == file_id,
                        ChatFileText.status == "processing",
                        ChatFileText.lease_token == token,
                        ChatFileText.lease_until > func.now(),
                    )
                    .values(**values, lease_token=None, lease_until=None, updated_at=func.now())
                    .returning(*ChatFileText.__table__.c)
                )
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None
