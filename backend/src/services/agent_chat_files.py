"""Ограниченное чтение файлов текущей переписки, включая её раннюю историю."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from src.agent.wakeup import wake_agent_worker
from src.clients.vision import DisabledVisionCapability, VisionCapability
from src.exceptions.access import AccessServiceError
from src.exceptions.agent_files import (
    AgentFileNotFoundError,
    AgentFilesServiceError,
    AgentFileValidationError,
)
from src.exceptions.auth import AuthServiceError
from src.exceptions.base import RepositoryError
from src.knowledge.extract import IMAGE_EXTENSIONS, INDEXABLE_EXTENSIONS, extract_indexable_text
from src.repositories.agent_chat_files import AgentChatFilesRepository
from src.repositories.chat_file_texts import ChatFileTextsRepository
from src.repositories.unit_of_work import UnitOfWork
from src.repositories.users import UsersRepository
from src.schemas.chat_files import (
    ChatFileListSchema,
    ChatFilePreparationSchema,
    ChatFileSchema,
    ChatFileTextSchema,
)
from src.services.access import AccessService
from src.services.auth import AuthService, Principal
from src.storage.task_attachments import TaskAttachmentStorage

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ChatFileContext:
    """Только сервер заполняет якорь текущей попытки; модель не выбирает чат."""

    user_id: int
    project_id: int | None
    message_id: int | None = None
    chat_turn_id: int | None = None
    run_id: UUID | None = None
    principal: Principal | None = None
    timezone: str = "UTC"


@dataclass(frozen=True, slots=True)
class AgentChatFilesScope:
    """Короткая DB-фаза; извлечение и чтение диска выполняются после её закрытия."""

    files: AgentChatFilesRepository
    texts: ChatFileTextsRepository
    users: UsersRepository
    auth: AuthService
    access: AccessService
    unit_of_work: UnitOfWork


AgentChatFilesScopeFactory = Callable[[], AbstractAsyncContextManager[AgentChatFilesScope]]


@dataclass(frozen=True, slots=True)
class ChatFileLocation:
    """Проверенная сервером переписка и включительная граница текущего вопроса."""

    kind: str
    scope_id: int
    upper_bound: int
    question_id: int | None = None
    project_id: int | None = None
    project_key: str | None = None

    def query(self) -> dict:
        return {"kind": self.kind, "scope_id": self.scope_id, "upper_bound": self.upper_bound}


def _read_original(storage: TaskAttachmentStorage, file: dict) -> bytes:
    """Оригиналы неизменяемы; точный серверный размер ограничивает файловый ввод."""
    with storage.resolve(file["storage_key"]).open("rb") as stream:
        content = stream.read(file["size"] + 1)
    if len(content) != file["size"]:
        raise ValueError("Размер оригинала изменился.")
    return content


def _page(content: str, *, offset: int, max_chars: int, q: str | None) -> dict:
    """Буквальный поиск сохраняет смещения исходных Unicode codepoints."""
    if not q:
        text = content[offset : offset + max_chars]
        end = offset + len(text)
        return {
            "text": text,
            "offset": offset,
            "next_offset": end if end < len(content) else None,
            "matches": [],
            "total_matches": None,
        }
    pattern = re.compile(re.escape(q), flags=re.IGNORECASE)
    total = 0
    first = None
    for match in pattern.finditer(content):
        total += 1
        if first is None and match.start() >= offset:
            first = match.start()
    if first is None:
        return {
            "text": "",
            "offset": offset,
            "next_offset": None,
            "matches": [],
            "total_matches": total,
        }
    start = max(offset, first - max_chars // 4)
    end = min(len(content), start + max_chars)
    matches = []
    omitted = False
    for match in pattern.finditer(content, start):
        if match.start() >= end:
            break
        if len(matches) == 20:
            omitted = True
            break
        matches.append({"start": match.start(), "end": match.end()})
    following = pattern.search(content, max(end, first + 1))
    return {
        "text": content[start:end],
        "offset": start,
        "next_offset": matches[-1]["end"] if omitted else end if following else None,
        "matches": matches,
        "total_matches": total,
    }


class AgentChatFilesService:
    """Общий reader private/shared файлов без переноса в постоянные документы."""

    def __init__(
        self,
        *,
        scope: AgentChatFilesScopeFactory,
        agent_storage: TaskAttachmentStorage,
        chat_storage: TaskAttachmentStorage,
        vision: VisionCapability,
        max_text_chars: int = 1_000_000,
        lease_seconds: int = 180,
        poll_seconds: float = 1.0,
        wait_seconds: float = 120.0,
    ) -> None:
        self.scope = scope
        self.agent_storage = agent_storage
        self.chat_storage = chat_storage
        self.vision = vision
        self.max_text_chars = max_text_chars
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds
        self.wait_seconds = wait_seconds

    @asynccontextmanager
    async def _scope(self):
        try:
            async with self.scope() as db:
                yield db
        except (AccessServiceError, AuthServiceError) as error:
            raise AgentFileNotFoundError(
                "Файл или текущий контекст переписки недоступен."
            ) from error
        except RepositoryError as error:
            raise AgentFilesServiceError("Не удалось прочитать файл переписки.") from error

    async def location(self, context: ChatFileContext) -> ChatFileLocation:
        """Общая актуальная авторизация чтения файлов и сообщений одной переписки."""
        async with self._scope() as db:
            return await self._require(db, context)

    async def _require(self, db: AgentChatFilesScope, context: ChatFileContext) -> ChatFileLocation:
        if context.run_id is None or (context.message_id is None) == (context.chat_turn_id is None):
            raise AgentFileNotFoundError("Нет активного контекста переписки.")
        if context.principal is not None:
            if context.principal.user_id != context.user_id:
                raise AgentFileNotFoundError("Контекст переписки недоступен.")
            principal = await db.auth.refresh_principal(context.principal)
            principal.require("agent:ask")
            if context.project_id is not None:
                principal.require("projects:read")
        else:
            user = await db.users.get_by_id(context.user_id)
            if user is None or not user.is_active:
                raise AgentFileNotFoundError("Пользователь недоступен.")
        if context.message_id is not None:
            kind = "conversation"
            row = await db.files.conversation_context(
                message_id=context.message_id, user_id=context.user_id
            )
        else:
            kind = "project_chat"
            row = await db.files.shared_context(
                chat_turn_id=context.chat_turn_id, user_id=context.user_id
            )
        if row is None:
            raise AgentFileNotFoundError("Попытка ответа или контекст переписки недоступен.")
        fixed_project = kind == "project_chat" or row["project_id"] is not None
        if (
            (fixed_project and row["project_id"] != context.project_id)
            or row["status"] != "processing"
            or row["run_id"] != context.run_id
        ):
            raise AgentFileNotFoundError("Попытка ответа или контекст переписки недоступен.")
        if context.project_id is not None:
            await db.access.ensure_project_access(
                project_id=context.project_id, user_id=context.user_id
            )
        return ChatFileLocation(
            kind,
            row["scope_id"],
            row["upper_bound"],
            row["question_id"],
            row["project_id"],
            row["project_key"],
        )

    @staticmethod
    async def _file(db: AgentChatFilesScope, location: ChatFileLocation, file_id: UUID) -> dict:
        file = await db.files.get_file(**location.query(), file_id=file_id)
        if file is None:
            raise AgentFileNotFoundError("Отправленный файл недоступен в текущей переписке.")
        return file

    @staticmethod
    def _metadata(file: dict, cached: dict | None = None) -> dict:
        return {
            **{
                key: file[key]
                for key in (
                    "file_id",
                    "original_name",
                    "content_type",
                    "size",
                    "message_id",
                    "created_at",
                )
            },
            "status": (cached or {}).get("status") or file.get("status") or "pending",
            "detail": (cached or {}).get("detail") or file.get("detail"),
            "original_chars": (cached or {}).get("original_chars")
            or file.get("original_chars")
            or 0,
            "truncated": bool((cached or {}).get("truncated") or file.get("truncated")),
            "content_hash": (cached or {}).get("content_hash") or file.get("content_hash"),
            "total_chars": (cached or {}).get("total_chars") or file.get("total_chars") or 0,
            "excerpt": file.get("excerpt") or None,
        }

    @staticmethod
    def _same_original(previous: dict, current: dict) -> bool:
        return all(
            previous[key] == current[key]
            for key in (
                "file_id",
                "storage_key",
                "original_name",
                "content_type",
                "size",
                "message_id",
            )
        )

    @staticmethod
    def _validate_page(*, offset: int, limit: int, maximum: int) -> None:
        if offset < 0 or not 1 <= limit <= maximum:
            raise AgentFileValidationError(f"offset >= 0; размер страницы от 1 до {maximum}.")

    @staticmethod
    def _query(q: str | None) -> str | None:
        value = q.strip() if q else None
        if value and len(value) > 200:
            raise AgentFileValidationError(
                "Поисковая строка должна содержать не более 200 символов."
            )
        return value or None

    async def list_files(
        self, *, context: ChatFileContext, q: str | None = None, limit: int = 20, offset: int = 0
    ) -> ChatFileListSchema:
        """Находит файлы всей доступной истории, без извлечения и чтения оригиналов."""
        self._validate_page(offset=offset, limit=limit, maximum=50)
        q = self._query(q)
        async with self._scope() as db:
            location = await self._require(db, context)
            total = await db.files.count_files(**location.query(), q=q)
            await self._require(db, context)
            files = await db.files.list_files(**location.query(), q=q, limit=limit, offset=offset)
            return ChatFileListSchema(
                items=[ChatFileSchema(**self._metadata(file)) for file in files],
                total=total,
                limit=limit,
                offset=offset,
                next_offset=offset + len(files) if offset + len(files) < total else None,
            )

    async def read_original(
        self, *, context: ChatFileContext, file_id: UUID
    ) -> tuple[ChatFileSchema, bytes]:
        """Server-only копирование доступного оригинала; не принимает пути модели."""
        async with self._scope() as db:
            location = await self._require(db, context)
            file = await self._file(db, location, file_id)
        storage = self.agent_storage if location.kind == "conversation" else self.chat_storage
        try:
            content = await asyncio.to_thread(_read_original, storage, file)
        except Exception as error:
            raise AgentFileNotFoundError("Оригинал файла недоступен.") from error
        await self._recheck(context, location, file)
        return ChatFileSchema(**self._metadata(file)), content

    async def _recheck(
        self, context: ChatFileContext, location: ChatFileLocation, file: dict
    ) -> None:
        async with self._scope() as db:
            current_location = await self._require(db, context)
            current = await self._file(db, current_location, file["file_id"])
            if current_location != location or not self._same_original(file, current):
                raise AgentFileNotFoundError("Файл или контекст переписки изменился.")

    async def read_text(
        self,
        *,
        context: ChatFileContext,
        file_id: UUID,
        offset: int = 0,
        max_chars: int = 8000,
        q: str | None = None,
    ) -> ChatFileTextSchema:
        """Читает только кэш; legacy-файл ставится в очередь без ожидания OCR."""
        self._validate_page(offset=offset, limit=max_chars, maximum=8000)
        q = self._query(q)
        async with self._scope() as db:
            location = await self._require(db, context)
            file = await self._file(db, location, file_id)
            cached = await db.texts.get(kind=location.kind, file_id=file_id)
            if cached is None:
                await db.texts.enqueue(kind=location.kind, file_ids=[file_id])
                await db.unit_of_work.commit()
                wake_agent_worker()
        cached = cached or {"status": "pending"}
        page = await asyncio.to_thread(
            _page, cached.get("content") or "", offset=offset, max_chars=max_chars, q=q
        )
        await self._recheck(context, location, file)
        metadata = self._metadata(file, cached)
        metadata["excerpt"] = None
        if metadata["status"] == "processing":
            metadata["detail"] = "Извлечение уже выполняется. Повторите чтение позже."
        elif metadata["status"] == "pending":
            metadata["detail"] = "Файл ожидает извлечения текста. Повторите чтение позже."
        return ChatFileTextSchema(
            **metadata,
            **page,
        )

    async def draft_status(
        self,
        *,
        kind: str,
        file_id: UUID,
        user_id: int,
        conversation_id: int | None = None,
        project_id: int | None = None,
        principal: Principal | None = None,
    ) -> ChatFilePreparationSchema:
        """Composer видит только состояние собственной загрузки, без текста и путей."""
        if kind not in {"conversation", "project_chat"}:
            raise AgentFileNotFoundError("Контекст загрузки недоступен.")
        async with self._scope() as db:
            if principal is not None:
                if principal.user_id != user_id:
                    raise AgentFileNotFoundError("Загрузка недоступна.")
                current = await db.auth.refresh_principal(principal)
                current.require("agent:ask")
                if project_id is not None:
                    current.require("projects:read")
            else:
                user = await db.users.get_by_id(user_id)
                if user is None or not user.is_active:
                    raise AgentFileNotFoundError("Пользователь недоступен.")
            file = await db.files.preparation_file(kind=kind, file_id=file_id)
            if (
                file is None
                or file["owner_user_id"] != user_id
                or file["project_id"] != project_id
                or (kind == "conversation" and file["scope_id"] != conversation_id)
                or (kind == "project_chat" and (project_id is None or conversation_id is not None))
            ):
                raise AgentFileNotFoundError("Загрузка недоступна.")
            if project_id is not None:
                await db.access.ensure_project_access(project_id=project_id, user_id=user_id)
            cached = await db.texts.get(kind=kind, file_id=file_id)
            if cached is None:
                await db.texts.enqueue(kind=kind, file_ids=[file_id])
                await db.unit_of_work.commit()
                wake_agent_worker()
            cached = cached or {"status": "pending"}
            return ChatFilePreparationSchema(
                file_id=file_id,
                status=cached["status"],
                detail=cached.get("detail"),
                total_chars=cached.get("total_chars", 0),
                original_chars=cached.get("original_chars", 0),
                truncated=cached.get("truncated", False),
            )

    async def _preparation_source(self, db, *, kind: str, file_id: UUID) -> dict | None:
        file = await db.files.preparation_file(kind=kind, file_id=file_id)
        if file is None:
            return None
        # Отправленный оригинал общего чата уже принадлежит истории команды.
        # Уход автора из проекта не отнимает файл у остальных участников.
        if kind == "project_chat" and file["message_id"] is not None:
            return file
        user = await db.users.get_by_id(file["owner_user_id"])
        if user is None or not user.is_active:
            return None
        if file["project_id"] is not None:
            try:
                await db.access.ensure_project_access(
                    project_id=file["project_id"], user_id=file["owner_user_id"]
                )
            except AccessServiceError:
                return None
        return file

    async def process_next(self) -> bool:
        """Одна durable подготовка; root запускает отдельно от очереди ответов Ариса."""
        token = uuid4()
        async with self._scope() as db:
            claimed = await db.texts.claim_next(token=token, lease_seconds=self.lease_seconds)
            if claimed is None:
                return False
            kind = "conversation" if claimed["agent_file_id"] is not None else "project_chat"
            file_id = claimed["agent_file_id"] or claimed["chat_attachment_id"]
            file = await self._preparation_source(db, kind=kind, file_id=file_id)
            await db.unit_of_work.commit()
        if file is None:
            async with self._scope() as db:
                await db.texts.finish(
                    kind=kind,
                    file_id=file_id,
                    token=token,
                    values={
                        "status": "failed",
                        "detail": "Загрузка или её владелец недоступны.",
                    },
                )
                await db.unit_of_work.commit()
            return True
        location = ChatFileLocation(kind, file["scope_id"], 0)
        stop = asyncio.Event()
        extraction = asyncio.create_task(self._extract(location, file))
        watcher = asyncio.create_task(self._watch_preparation(kind, file_id, token, stop))
        try:
            done, _ = await asyncio.wait({extraction, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if watcher in done and not watcher.result():
                extraction.cancel()
                return True
            values = await extraction
            async with self._scope() as db:
                current = await self._preparation_source(db, kind=kind, file_id=file_id)
                if current is None or not self._same_preparation(file, current):
                    # Удалённый draft исчез вместе с cache; поздний результат не воскрешает его.
                    await db.texts.finish(
                        kind=kind,
                        file_id=file_id,
                        token=token,
                        values={
                            "status": "failed",
                            "detail": "Загрузка или её доступ изменились во время обработки.",
                        },
                    )
                else:
                    await db.texts.finish(kind=kind, file_id=file_id, token=token, values=values)
                await db.unit_of_work.commit()
            return True
        finally:
            stop.set()
            extraction.cancel()
            await asyncio.gather(extraction, watcher, return_exceptions=True)

    @staticmethod
    def _same_preparation(previous: dict, current: dict) -> bool:
        # Send меняет только message_id; это допустимый bind того же original/cache.
        return all(
            previous[key] == current[key]
            for key in (
                "file_id",
                "storage_key",
                "original_name",
                "content_type",
                "size",
                "scope_id",
                "project_id",
                "owner_user_id",
            )
        )

    async def _watch_preparation(
        self, kind: str, file_id: UUID, token: UUID, stop: asyncio.Event
    ) -> bool:
        """Каскадное удаление отменяет локальное ожидание; lease переживает долгий vision."""
        try:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(
                        stop.wait(), timeout=min(self.poll_seconds, self.lease_seconds / 3)
                    )
                    return True
                except TimeoutError:
                    pass
                async with self._scope() as db:
                    owned = await db.texts.renew(
                        kind=kind,
                        file_id=file_id,
                        token=token,
                        lease_seconds=self.lease_seconds,
                    )
                    await db.unit_of_work.commit()
                if not owned:
                    return False
            return True
        except Exception:
            logger.warning("Не удалось продлить lease извлечения файла чата %s.", file_id)
            return False

    async def cleanup_agent_drafts(self, *, retention_hours: int = 24) -> int:
        """TTL личных загрузок сериализуется с Send и не затрагивает sent originals."""
        cutoff = datetime.now(UTC) - timedelta(hours=retention_hours)
        async with self._scope() as db:
            candidates = await db.files.expired_conversation_drafts(cutoff=cutoff)
        removed = 0
        for file_id in candidates:
            async with self._scope() as db:
                deleted = await db.files.delete_expired_conversation_draft(
                    file_id=file_id, cutoff=cutoff
                )
                await db.unit_of_work.commit()
            if deleted is not None:
                await self.agent_storage.delete_if_exists(deleted["storage_key"])
                removed += 1
        # Оборванная запись до INSERT или сбой удаления после commit оставляют
        # только старый файл на диске. Свежие и любые привязанные оригиналы сохраняются.
        async for keys in self.agent_storage.old_key_batches(cutoff.timestamp()):
            async with self._scope() as db:
                existing = await db.files.existing_conversation_keys(keys=keys)
            for key in set(keys) - existing:
                await self.agent_storage.delete_if_exists(key)
                removed += 1
        return removed

    async def _extract(self, location: ChatFileLocation, file: dict) -> dict:
        values = {
            "status": "ready",
            "detail": None,
            "content": "",
            "original_hash": None,
            "content_hash": None,
            "total_chars": 0,
            "original_chars": 0,
            "truncated": False,
        }
        suffix = Path(file["original_name"]).suffix.lower()
        if suffix not in INDEXABLE_EXTENSIONS:
            return {
                **values,
                "status": "unsupported",
                "detail": "Формат не поддерживает извлечение текста.",
            }
        if suffix in IMAGE_EXTENSIONS and isinstance(self.vision, DisabledVisionCapability):
            return {
                **values,
                "status": "disabled",
                "detail": "Распознавание изображений выключено.",
            }
        storage = self.agent_storage if location.kind == "conversation" else self.chat_storage
        try:
            original = await asyncio.to_thread(_read_original, storage, file)
            values["original_hash"] = hashlib.sha256(original).hexdigest()
            text = await extract_indexable_text(file["original_name"], original, vision=self.vision)
            if not text:
                return {
                    **values,
                    "status": "empty",
                    "detail": "Текст не найден. У PDF читается только текстовый слой, OCR страниц не выполняется.",
                }
            values["original_chars"] = len(text)
            values["truncated"] = len(text) > self.max_text_chars
            text = text[: self.max_text_chars]
            return {
                **values,
                "content": text,
                "total_chars": len(text),
                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "detail": "Текст ограничен серверным пределом извлечения."
                if values["truncated"]
                else None,
            }
        except Exception:
            return {
                **values,
                "status": "failed",
                "detail": "Не удалось прочитать или извлечь текст файла. Загрузите файл повторно или используйте другой формат.",
            }
