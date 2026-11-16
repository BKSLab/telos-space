"""SQL Git-источников. Публичная операция — один statement, commit принадлежит сервису."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import and_, delete, exists, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.git_sources import (
    GitChange,
    GitConnection,
    GitDelivery,
    GitFile,
    GitFragment,
    GitSnapshot,
    GitSyncJob,
    GitTaskChangeLink,
)
from src.db.models.project_members import ProjectMember, ProjectRole
from src.db.models.projects import Project
from src.db.models.tasks import Task
from src.db.models.users import User
from src.exceptions.git_sources import GitSourcesRepositoryError

C, S, F, R, J, D, G, L = (
    model.__table__
    for model in (
        GitConnection,
        GitSnapshot,
        GitFile,
        GitFragment,
        GitSyncJob,
        GitDelivery,
        GitChange,
        GitTaskChangeLink,
    )
)
P, T, U, M = (model.__table__ for model in (Project, Task, User, ProjectMember))


class GitSourcesRepository:
    """Хранит snapshots/checkpoints и делает scoped FTS, не выполняя сетевые вызовы."""

    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def _execute(self, statement):
        try:
            return await self.db_session.execute(statement)
        except SQLAlchemyError:
            await self.db_session.rollback()
        raise GitSourcesRepositoryError("Ошибка хранилища Git-источников.")

    async def _one(self, statement) -> dict | None:
        row = (await self._execute(statement)).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def _many(self, statement) -> list[dict]:
        return [dict(row) for row in (await self._execute(statement)).mappings().all()]

    @staticmethod
    def _visible(*, project_id: int, is_owner: bool = True):
        result = C.c.project_id == project_id
        if not is_owner:
            result = and_(result, or_(~C.c.is_private, C.c.share_private_with_project))
        return result

    async def get_connection(
        self, *, project_id: int, connection_id: int, for_update: bool = False
    ) -> dict | None:
        """Читает подключение только внутри проекта, при необходимости блокирует его."""
        statement = select(C).where(C.c.id == connection_id, C.c.project_id == project_id)
        return await self._one(statement.with_for_update() if for_update else statement)

    async def get_connections(
        self, *, project_id: int, connection_ids: set[int]
    ) -> list[dict]:
        """Locks one publication cohort and its credential owner after remote probes."""
        if not connection_ids:
            return []
        return await self._many(
            select(C, P.c.owner_id.label("project_owner_id"))
            .select_from(C.join(P, P.c.id == C.c.project_id))
            .where(C.c.project_id == project_id, C.c.id.in_(connection_ids))
            .order_by(C.c.id)
            .with_for_update(read=True, of=(P, C))
        )

    async def list_connections(
        self, *, project_id: int, is_owner: bool, limit: int, offset: int = 0
    ) -> list[dict]:
        """Возвращает стабильную страницу метаданных разрешённых project bindings."""
        return await self._many(
            select(C)
            .where(self._visible(project_id=project_id, is_owner=is_owner))
            .order_by(C.c.id)
            .limit(limit)
            .offset(offset)
        )

    async def count_connections(self, *, project_id: int, is_owner: bool) -> int:
        """Считает разрешённые подключения SQL-агрегатом."""
        return int(
            (
                await self._execute(
                    select(func.count())
                    .select_from(C)
                    .where(self._visible(project_id=project_id, is_owner=is_owner))
                )
            ).scalar_one()
        )

    async def insert_connection(self, *, data: dict) -> dict:
        """Сохраняет binding и возвращает серверные значения без дочитывания."""
        return await self._one(insert(C).values(**data).returning(C))

    async def update_connection(
        self,
        *,
        project_id: int,
        connection_id: int,
        data: dict,
        expected_revision: int | None = None,
        expected_generation: int | None = None,
        lease_id: UUID | None = None,
    ) -> dict | None:
        """Изменяет только выбранную connection с заданными CAS условиями."""
        conditions = [C.c.id == connection_id, C.c.project_id == project_id]
        if expected_revision is not None:
            conditions.append(C.c.revision == expected_revision)
        if expected_generation is not None:
            conditions.append(C.c.generation == expected_generation)
        if lease_id is not None:
            conditions.append(C.c.lease_id == lease_id)
        return await self._one(
            update(C).where(*conditions).values(**data, updated_at=func.now()).returning(C)
        )

    async def claim_connections(
        self, *, lease_id: UUID, lease_seconds: int, limit: int
    ) -> list[dict]:
        """Атомарно выдаёт due connections через SKIP LOCKED; crash восстанавливается по lease."""
        eligible = (
            select(C.c.id)
            .where(
                C.c.enabled,
                C.c.next_poll_at <= func.now(),
                or_(C.c.lease_until.is_(None), C.c.lease_until < func.now()),
            )
            .order_by(C.c.next_poll_at, C.c.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        return await self._many(
            update(C)
            .where(C.c.id.in_(eligible))
            .values(lease_id=lease_id, lease_until=func.now() + timedelta(seconds=lease_seconds))
            .returning(C)
        )

    async def worker_identity(self, *, connection_id: int) -> dict | None:
        """Читает активного текущего владельца, не произвольного пользователя из job."""
        statement = (
            select(U.c.id, U.c.username, U.c.last_name, U.c.first_name, U.c.middle_name)
            .select_from(
                C.join(P, P.c.id == C.c.project_id)
                .join(U, U.c.id == P.c.owner_id)
                .join(M, and_(M.c.project_id == P.c.id, M.c.user_id == U.c.id))
            )
            .where(C.c.id == connection_id, U.c.is_active, M.c.role == ProjectRole.OWNER)
        )
        return await self._one(statement)

    async def get_snapshot(self, *, connection_id: int, sha: str, scope_hash: str) -> dict | None:
        """Читает immutable commit+scope snapshot своего подключения."""
        return await self._one(
            select(S).where(
                S.c.connection_id == connection_id,
                S.c.commit_sha == sha,
                S.c.scope_hash == scope_hash,
            )
        )

    async def insert_snapshot(self, *, data: dict) -> dict | None:
        """Создаёт snapshot без замены уже существующего manifest."""
        return await self._one(
            pg_insert(S)
            .values(**data)
            .on_conflict_do_nothing(constraint="uq_git_snapshot_identity")
            .returning(S)
        )

    async def update_snapshot(self, *, snapshot_id: int, data: dict) -> dict | None:
        """Сохраняет подготовку/охват; completed snapshot не переписывается."""
        return await self._one(
            update(S)
            .where(S.c.id == snapshot_id, ~S.c.complete)
            .values(**data, updated_at=func.now())
            .returning(S)
        )

    async def get_job(self, *, connection_id: int, generation: int) -> dict | None:
        """Читает checkpoint только точного поколения."""
        return await self._one(
            select(J).where(J.c.connection_id == connection_id, J.c.generation == generation)
        )

    async def insert_job(self, *, data: dict) -> dict | None:
        """Дедуплицирует durable работу одного поколения."""
        return await self._one(
            pg_insert(J)
            .values(**data)
            .on_conflict_do_nothing(constraint="uq_git_job_generation")
            .returning(J)
        )

    async def update_job(self, *, job_id: int, data: dict) -> dict | None:
        """Обновляет checkpoint в общей транзакции с подготовленным файлом."""
        return await self._one(
            update(J).where(J.c.id == job_id).values(**data, updated_at=func.now()).returning(J)
        )

    async def supersede_jobs(self, *, connection_id: int, generation: int) -> None:
        """Старые поколения не превращаются в текущую публикацию."""
        await self._execute(
            update(J)
            .where(
                J.c.connection_id == connection_id,
                J.c.generation != generation,
                J.c.state == "pending",
            )
            .values(state="superseded", updated_at=func.now())
        )

    @staticmethod
    def _file_statement(*, include_text: bool = True):
        columns = [column for column in F.c if include_text or column.name != "text"]
        return select(
            *columns,
            S.c.commit_sha,
            S.c.scope_hash,
            S.c.complete.label("snapshot_complete"),
            S.c.tree_truncated,
            C.c.project_id,
            C.c.repository_id,
            C.c.owner,
            C.c.repo,
            C.c.ref,
            C.c.generation,
            C.c.credential_revision,
            C.c.indexed_sha,
            C.c.observed_sha,
        ).select_from(
            F.join(S, and_(S.c.id == F.c.snapshot_id, S.c.connection_id == F.c.connection_id)).join(
                C, C.c.id == S.c.connection_id
            )
        )

    async def get_file(
        self,
        *,
        project_id: int,
        connection_id: int,
        file_id: int | None = None,
        sha: str | None = None,
        path: str | None = None,
    ) -> dict | None:
        """Source ID/path проверяются join с текущим проектом и scope hash."""
        statement = self._file_statement().where(
            C.c.project_id == project_id, C.c.id == connection_id, S.c.scope_hash == C.c.scope_hash
        )
        if file_id is not None:
            statement = statement.where(F.c.id == file_id)
        if sha is not None:
            statement = statement.where(S.c.commit_sha == sha)
        if path is not None:
            statement = statement.where(F.c.path == path)
        return await self._one(statement)

    async def get_snapshot_file(self, *, snapshot_id: int, path: str) -> dict | None:
        """Проверяет idempotent checkpoint без сетевого повторения blob."""
        return await self._one(select(F).where(F.c.snapshot_id == snapshot_id, F.c.path == path))

    async def reusable_file(
        self, *, connection_id: int, blob_sha: str, scope_hash: str
    ) -> dict | None:
        """Переиспользует только проверенный blob прежней версии той же политики/connection."""
        return await self._one(
            select(F)
            .select_from(F.join(S, S.c.id == F.c.snapshot_id))
            .where(
                F.c.connection_id == connection_id,
                F.c.blob_sha == blob_sha,
                or_(
                    F.c.state == "ready",
                    and_(
                        F.c.state == "excluded",
                        F.c.reason.in_(
                            ["binary_file", "unsupported_encoding", "lfs_pointer", "secret_content"]
                        ),
                    ),
                ),
                S.c.scope_hash == scope_hash,
            )
            .order_by(F.c.id.desc())
            .limit(1)
        )

    async def insert_file(self, *, data: dict) -> dict | None:
        """Не заменяет байты и identity уже сохранённого файла версии."""
        return await self._one(
            pg_insert(F)
            .values(**data)
            .on_conflict_do_nothing(constraint="uq_git_file_snapshot_path")
            .returning(F)
        )

    async def insert_fragments(self, *, file_id: int, fragments: list[dict]) -> None:
        """Пакетно сохраняет bounded fragments/FTS в той же транзакции, без N+1 SQL."""
        if not fragments:
            return
        rows = [
            {
                "file_id": file_id,
                **fragment,
                "search_vector": func.to_tsvector("simple", fragment["text"]),
            }
            for fragment in fragments
        ]
        await self._execute(
            pg_insert(R).values(rows).on_conflict_do_nothing(constraint="uq_git_fragment_ordinal")
        )

    async def snapshot_coverage(self, *, snapshot_id: int) -> dict:
        """Считает охват и объём SQL-агрегатами."""
        return await self._one(
            select(
                func.count().label("processed"),
                func.count().filter(F.c.state == "ready").label("indexed_files"),
                func.count().filter(F.c.state == "excluded").label("excluded_files"),
                func.count().filter(F.c.reason == "snapshot_byte_limit").label("budget_excluded"),
                func.count().filter(F.c.state == "error").label("failed_files"),
                func.coalesce(func.sum(F.c.size).filter(F.c.state == "ready"), 0).label(
                    "bytes_processed"
                ),
            ).where(F.c.snapshot_id == snapshot_id)
        )

    async def search(
        self, *, project_id: int, connection_ids: tuple[int, ...], query: str, limit: int
    ) -> list[dict]:
        """FTS кандидатов только live-proof проверенных connections, с immutable координатами."""
        terms = func.plainto_tsquery("simple", query)
        rank = func.ts_rank_cd(R.c.search_vector, terms)
        base = (
            self._file_statement(include_text=False)
            .add_columns(
                R.c.text.label("excerpt"),
                R.c.offset.label("fragment_offset"),
                R.c.line_start,
                R.c.line_end,
                rank.label("rank"),
                func.row_number().over(partition_by=F.c.id, order_by=rank.desc()).label("rn"),
            )
            .join(R, R.c.file_id == F.c.id)
            .where(
                C.c.project_id == project_id,
                C.c.id.in_(connection_ids),
                C.c.enabled,
                S.c.scope_hash == C.c.scope_hash,
                S.c.commit_sha == func.coalesce(C.c.indexed_sha, C.c.observed_sha),
                F.c.state == "ready",
                R.c.search_vector.op("@@")(terms),
            )
            .subquery()
        )
        return await self._many(
            select(base).where(base.c.rn == 1).order_by(base.c.rank.desc(), base.c.id).limit(limit)
        )

    async def map_files(
        self, *, project_id: int, connection_ids: tuple[int, ...], limit: int
    ) -> list[dict]:
        """Читает bounded карту без загрузки полного текста файлов."""
        return await self._many(
            self._file_statement(include_text=False)
            .where(
                C.c.project_id == project_id,
                C.c.id.in_(connection_ids),
                C.c.enabled,
                S.c.scope_hash == C.c.scope_hash,
                S.c.commit_sha == func.coalesce(C.c.indexed_sha, C.c.observed_sha),
                F.c.state == "ready",
            )
            .order_by(C.c.id, F.c.path)
            .limit(limit)
        )

    async def count_map_files(self, *, project_id: int, connection_ids: tuple[int, ...]) -> int:
        """Считает компоненты текущего набора версий без выгрузки корпуса."""
        statement = (
            select(func.count())
            .select_from(
                F.join(
                    S, and_(S.c.id == F.c.snapshot_id, S.c.connection_id == F.c.connection_id)
                ).join(C, C.c.id == F.c.connection_id)
            )
            .where(
                C.c.project_id == project_id,
                C.c.id.in_(connection_ids),
                C.c.enabled,
                S.c.scope_hash == C.c.scope_hash,
                S.c.commit_sha == func.coalesce(C.c.indexed_sha, C.c.observed_sha),
                F.c.state == "ready",
            )
        )
        return int((await self._execute(statement)).scalar_one())

    async def files_by_ids(
        self, *, project_id: int, connection_id: int, file_ids: set[int]
    ) -> list[dict]:
        """Пакетно перечитывает источники публикации под текущей project/scope identity."""
        return await self._many(
            self._file_statement().where(
                C.c.project_id == project_id,
                C.c.id == connection_id,
                S.c.scope_hash == C.c.scope_hash,
                F.c.id.in_(file_ids),
            )
        )

    async def get_delivery(self, *, connection_id: int, delivery_id: str) -> dict | None:
        """Читает квитанцию после проверки HMAC в сервисе."""
        return await self._one(
            select(D).where(D.c.connection_id == connection_id, D.c.delivery_id == delivery_id)
        )

    async def insert_delivery(self, *, data: dict) -> dict | None:
        """Атомарно дедуплицирует delivery, не сохраняя исходное тело события."""
        return await self._one(
            pg_insert(D)
            .values(**data)
            .on_conflict_do_nothing(constraint="uq_git_delivery")
            .returning(D)
        )

    async def get_task_key(self, *, project_id: int, task_id: int) -> dict | None:
        """Читает server task key для осторожных suggested matches."""
        return await self._one(
            select(T.c.id, T.c.number, P.c.key)
            .select_from(T.join(P, P.c.id == T.c.project_id))
            .where(T.c.id == task_id, T.c.project_id == project_id)
        )

    async def get_change(self, *, connection_id: int, change_id: int) -> dict | None:
        """Читает изменение только своей connection."""
        return await self._one(
            select(G).where(G.c.connection_id == connection_id, G.c.id == change_id)
        )

    async def find_change(self, *, connection_id: int, kind: str, provider_id: str) -> dict | None:
        """Находит общую N:N identity PR/commit."""
        return await self._one(
            select(G).where(
                G.c.connection_id == connection_id, G.c.kind == kind, G.c.provider_id == provider_id
            )
        )

    async def insert_change(self, *, data: dict) -> dict | None:
        """Повтор ручной привязки не дублирует изменение."""
        return await self._one(
            pg_insert(G)
            .values(**data)
            .on_conflict_do_nothing(constraint="uq_git_change_identity")
            .returning(G)
        )

    async def update_change(
        self,
        *,
        connection_id: int,
        change_id: int,
        data: dict,
        expected_revision: int | None = None,
    ) -> dict | None:
        """Обновляет authoritative status и checkpoint с CAS версии."""
        statement = update(G).where(G.c.connection_id == connection_id, G.c.id == change_id)
        if expected_revision is not None:
            statement = statement.where(G.c.revision == expected_revision)
        return await self._one(statement.values(**data, updated_at=func.now()).returning(G))

    async def due_changes(self, *, connection_id: int, limit: int = 1) -> list[dict]:
        """Поздние Git/CI/deploy факты проверяются независимо от task status."""
        return await self._many(
            select(G)
            .where(G.c.connection_id == connection_id, G.c.next_refresh_at <= func.now())
            .order_by(G.c.next_refresh_at, G.c.id)
            .limit(limit)
        )

    async def insert_task_link(self, *, task_id: int, change_id: int, user_id: int) -> dict | None:
        """Явная N:N привязка без изменения стадии задачи."""
        return await self._one(
            pg_insert(L)
            .values(task_id=task_id, change_id=change_id, created_by_user_id=user_id)
            .on_conflict_do_nothing(constraint="uq_git_task_change_link")
            .returning(L)
        )

    async def list_task_links(self, *, project_id: int, task_id: int) -> list[dict]:
        """Текущий task.project_id и connection.project_id обязаны совпадать."""
        return await self._many(
            select(
                G,
                L.c.id.label("link_id"),
                L.c.task_id,
                C.c.repository_id,
                C.c.owner,
                C.c.repo,
                C.c.generation,
                C.c.credential_revision,
            )
            .select_from(
                L.join(G, G.c.id == L.c.change_id)
                .join(C, C.c.id == G.c.connection_id)
                .join(T, T.c.id == L.c.task_id)
            )
            .where(
                L.c.task_id == task_id, T.c.project_id == project_id, C.c.project_id == project_id
            )
            .order_by(C.c.id, G.c.id)
        )

    async def links_by_ids(self, *, project_id: int, link_ids: set[int]) -> list[dict]:
        """Пакетно проверяет наличие task relationships после удаления/переноса задачи."""
        return await self._many(
            select(L, G.c.connection_id)
            .select_from(
                L.join(G, G.c.id == L.c.change_id)
                .join(C, C.c.id == G.c.connection_id)
                .join(T, T.c.id == L.c.task_id)
            )
            .where(L.c.id.in_(link_ids), T.c.project_id == project_id, C.c.project_id == project_id)
        )

    async def request_evidence_refresh(
        self, *, connection_id: int, invalidate: bool = False
    ) -> None:
        """Queues a bounded provider check; a new credential never inherits old facts."""
        values = {"next_refresh_at": func.now()}
        if invalidate:
            values.update(
                evidence=[],
                evidence_partial=True,
                refresh_state={},
                revision=G.c.revision + 1,
                last_error_code=None,
            )
        await self._execute(
            update(G)
            .where(G.c.connection_id == connection_id)
            .values(**values, updated_at=func.now())
        )

    async def delete_task_link(self, *, project_id: int, task_id: int, link_id: int) -> bool:
        """Удаляет только связь задачи данного проекта, сохраняя историю изменения."""
        valid = exists(
            select(1)
            .select_from(T.join(G, G.c.id == L.c.change_id).join(C, C.c.id == G.c.connection_id))
            .where(
                T.c.id == L.c.task_id, T.c.project_id == project_id, C.c.project_id == project_id
            )
        )
        return (
            await self._execute(
                delete(L).where(L.c.id == link_id, L.c.task_id == task_id, valid).returning(L.c.id)
            )
        ).scalar_one_or_none() is not None
