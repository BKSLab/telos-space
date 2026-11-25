"""SQL Git-источников. Публичная операция — один statement, commit принадлежит сервису."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import (
    and_,
    cast,
    delete,
    exists,
    func,
    insert,
    literal,
    or_,
    select,
    text,
    true,
    update,
)
from sqlalchemy import case as sa_case
from sqlalchemy.dialects.postgresql import JSONPATH
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
from src.db.models.git_structure import GitAnalysis, GitRelation, GitSymbol
from src.db.models.project_members import ProjectMember, ProjectRole
from src.db.models.project_stages import ProjectStage
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


A, Y, E = (model.__table__ for model in (GitAnalysis, GitSymbol, GitRelation))


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

    async def github_profile(self, user_id: int, *, lock: bool = False) -> dict | None:
        statement = select(U.c.github_token_ciphertext, U.c.github_account_id, U.c.github_login)
        statement = statement.where(U.c.id == user_id, U.c.is_active)
        return await self._one(statement.with_for_update() if lock else statement)

    async def update_github_profile(
        self, user_id: int, *, ciphertext: str | None, account_id: int | None, login: str | None
    ) -> None:
        await self._execute(
            update(U).where(U.c.id == user_id, U.c.is_active).values(
                github_token_ciphertext=ciphertext, github_account_id=account_id, github_login=login,
            )
        )

    async def invalidate_profile_connections(self, user_id: int, *, connected: bool) -> list[dict]:
        return await self._many(
            update(C).where(
                C.c.project_id.in_(select(P.c.id).where(P.c.owner_id == user_id))
            ).values(
                credential_owner_id=user_id if connected else None,
                credential_revision=C.c.credential_revision + 1,
                revision=C.c.revision + 1,
                state=sa_case((C.c.enabled, "access_lost"), else_="disabled"),
                last_error_code="github_profile_pending" if connected else "github_profile_required",
                next_poll_at=func.now(),
                lease_id=None,
                lease_until=None,
            ).returning(C.c.id, C.c.project_id)
        )

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
        statement = select(C, P.c.owner_id.label("project_owner_id")).join(P, P.c.id == C.c.project_id).where(
            C.c.id == connection_id, C.c.project_id == project_id)
        return await self._one(statement.with_for_update(of=(P, C)) if for_update else statement)

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

    async def verification_candidates(self, *, project_id: int, after_id: int, limit: int) -> list[dict]:
        """Кандидаты на live proof. Курсор задаётся уже видимым ID подключения."""
        return await self._many(select(C.c.id).where(
            C.c.project_id == project_id, C.c.id > after_id, C.c.enabled,
            or_(~C.c.is_private, C.c.share_private_with_project),
            C.c.state.not_in(("access_lost", "disabled", "error")),
        ).order_by(C.c.id).limit(limit))

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

    async def valid_stage_ids(self, *, project_id: int, stage_ids: list[int]) -> set[int]:
        """Validate configured stage IDs against the owning project."""
        result = await self._execute(
            select(ProjectStage.id).where(
                ProjectStage.project_id == project_id, ProjectStage.id.in_(stage_ids)
            )
        )
        return set(result.scalars())

    async def claim_connections(
        self, *, lease_id: UUID, lease_seconds: int, limit: int
    ) -> list[dict]:
        """Атомарно выдаёт due connections через SKIP LOCKED; crash восстанавливается по lease."""
        eligible = (
            select(C.c.id)
            .where(
                C.c.enabled,
                or_(C.c.last_error_code.is_(None), C.c.last_error_code != "github_profile_required"),
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
            .values(
                lease_id=lease_id,
                lease_until=func.now() + timedelta(seconds=lease_seconds),
                lease_sync_revision=C.c.sync_requested_revision,
                last_sync_reason=sa_case(
                    (C.c.sync_requested_revision == C.c.sync_completed_revision, "poll"),
                    else_=C.c.last_sync_reason,
                ),
                last_sync_requested_at=sa_case(
                    (C.c.sync_requested_revision == C.c.sync_completed_revision, func.now()),
                    else_=C.c.last_sync_requested_at,
                ),
            )
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
    def _file_statement(*, include_text: bool = True, include_analysis: bool = True):
        columns = [column for column in F.c if (include_text or column.name != "text")
                   and (include_analysis or column.name != "analysis")]
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

    async def snapshot_file_states(self, *, snapshot_id: int, paths: list[str]) -> list[dict]:
        """Return processing states for the bounded tree page, including metadata/errors."""
        return await self._many(
            select(F.c.path, F.c.state, F.c.reason).where(
                F.c.snapshot_id == snapshot_id, F.c.path.in_(paths)
            )
        )

    async def reusable_file(
        self, *, connection_id: int, blob_sha: str, scope_hash: str
    ) -> dict | None:
        """Переиспользует готовый текст того же blob/connection; metadata требует той же политики."""
        return await self._one(
            select(F)
            .select_from(F.join(S, S.c.id == F.c.snapshot_id))
            .where(
                F.c.connection_id == connection_id,
                F.c.blob_sha == blob_sha,
                or_(
                    F.c.state == "ready",
                    and_(
                        F.c.state == "metadata",
                        F.c.reason.in_(["binary_file", "unsupported_encoding", "lfs_pointer"]),
                        S.c.scope_hash == scope_hash,
                    ),
                ),
            )
            .order_by(F.c.id.desc())
            .limit(1)
        )

    async def insert_file(self, *, data: dict) -> dict | None:
        """Retry failed extraction without replacing a successfully processed immutable blob."""
        statement = pg_insert(F).values(**data)
        return await self._one(
            statement.on_conflict_do_update(
                constraint="uq_git_file_snapshot_path",
                set_={key: value for key, value in data.items() if key not in {"snapshot_id", "connection_id", "path"}},
                where=F.c.state == "error",
            ).returning(F)
        )

    async def insert_fragments(self, *, file_id: int, fragments: list[dict]) -> None:
        """Пакетно сохраняет bounded fragments/FTS в той же транзакции, без N+1 SQL."""
        if not fragments:
            return
        rows = [
            {
                "file_id": file_id,
                **{field: fragment[field] for field in ("ordinal", "offset", "line_start", "line_end", "text")},
                "search_vector": func.to_tsvector("simple", fragment["text"]),
            }
            for fragment in fragments
        ]
        await self._execute(
            pg_insert(R).values(rows).on_conflict_do_nothing(constraint="uq_git_fragment_ordinal")
        )

    async def snapshot_coverage(self, *, snapshot_id: int) -> dict:
        """Считает охват и безопасные категории исключений SQL-агрегатами."""
        grouped = (
            select(F.c.reason, func.count().label("count"))
            .where(F.c.snapshot_id == snapshot_id, F.c.state == "metadata", F.c.reason.is_not(None))
            .group_by(F.c.reason).subquery()
        )
        exclusions = select(func.jsonb_object_agg(grouped.c.reason, grouped.c.count)).scalar_subquery()
        return await self._one(
            select(
                func.coalesce(exclusions, func.jsonb_build_object()).label("metadata_counts"),
                func.count().label("processed"),
                func.count().filter(F.c.state == "ready").label("indexed_files"),
                func.count().filter(F.c.state == "metadata").label("metadata_files"),
                func.count().filter(F.c.state == "error").label("failed_files"),
                func.coalesce(func.sum(F.c.size).filter(F.c.state == "ready"), 0).label(
                    "bytes_processed"
                ),
            ).where(F.c.snapshot_id == snapshot_id)
        )

    async def search(
        self, *, project_id: int, connection_ids: tuple[int, ...], query: str, limit: int,
        sha: str | None = None, analyzer_version: str | None = None,
    ) -> list[dict]:
        """FTS кандидатов только live-proof проверенных connections, с immutable координатами."""
        filter_scope = (
            C.c.project_id == project_id,
            C.c.id.in_(connection_ids),
            C.c.enabled,
            S.c.scope_hash == C.c.scope_hash,
            S.c.commit_sha == (sha or func.coalesce(C.c.indexed_sha, C.c.observed_sha)),
            F.c.state == "ready",
        )
        # PostgreSQL computes all offsets from original Unicode text. A small
        # candidate set for each strategy keeps the merged ranking deterministic.
        exact = await self._many(
            self._file_statement(include_text=False, include_analysis=False)
            .add_columns(
                func.left(F.c.text, 2000).label("excerpt"),
                literal(0).label("fragment_offset"),
                literal(1).label("line_start"),
            )
            .where(*filter_scope, F.c.path == query)
            .order_by(C.c.id, F.c.id)
            .limit(limit)
        )
        normalized = "".join(ch for ch in query.lower() if ch.isalnum())[:256]
        matches = []
        if analyzer_version and normalized:
            grams = list(dict.fromkeys(normalized[i : i + 3] for i in range(len(normalized) - 2)))[
                :16
            ]
            start = Y.c.search_name.startswith(normalized, autoescape=True)
            gram_votes = sum(
                sa_case((Y.c.search_name.contains(gram, autoescape=True), 1), else_=0)
                for gram in grams
            )
            fuzzy = gram_votes >= max(2, (len(grams) + 1) // 2) if grams else False
            rank = sa_case(
                (Y.c.qualified_name == query, 0),
                (Y.c.name == query, 1),
                (Y.c.search_name == normalized, 2),
                (start, 3),
                else_=4,
            )
            published = (
                select(A.c.id)
                .where(A.c.snapshot_id == S.c.id, A.c.state == "published")
                .order_by((A.c.version == analyzer_version).desc(), A.c.id.desc())
                .limit(1)
                .correlate(S)
                .scalar_subquery()
            )
            stmt = (
                self._file_statement(include_text=False, include_analysis=False)
                .join(A, A.c.id == published)
                .join(Y, and_(Y.c.analysis_id == A.c.id, Y.c.path == F.c.path))
                .add_columns(
                    Y.c.id.label("symbol_id"),
                    Y.c.qualified_name,
                    Y.c.kind,
                    Y.c.offset.label("fragment_offset"),
                    Y.c.line_start,
                    func.substr(
                        F.c.text, Y.c.offset + 1, func.least(2000, Y.c.end_offset - Y.c.offset)
                    ).label("excerpt"),
                )
                .where(
                    *filter_scope, or_(Y.c.qualified_name == query, Y.c.name == query, start, fuzzy)
                )
            )
            ordering = [rank, -gram_votes] if grams else [rank]
            matches = await self._many(stmt.order_by(*ordering, C.c.id, F.c.path, Y.c.offset, Y.c.id).limit(limit))
        terms = func.plainto_tsquery("simple", query)
        rank = func.ts_rank_cd(R.c.search_vector, terms)
        base = (
            self._file_statement(include_text=False, include_analysis=False)
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
                S.c.commit_sha == (sha or func.coalesce(C.c.indexed_sha, C.c.observed_sha)),
                F.c.state == "ready",
                R.c.search_vector.op("@@")(terms),
            )
            .subquery()
        )
        fragments = await self._many(
            select(base).where(base.c.rn == 1).order_by(base.c.rank.desc(), base.c.id).limit(limit)
        )
        seen = set()
        result = []
        for item in [*exact, *matches, *fragments]:
            key = (
                item["connection_id"],
                item["commit_sha"],
                item["id"],
                item["fragment_offset"],
                len(item["excerpt"]),
            )
            if key not in seen:
                result.append(item)
                seen.add(key)
        return result[:limit]

    async def map_files(
        self, *, project_id: int, connection_ids: tuple[int, ...], limit: int
    ) -> list[dict]:
        """Читает bounded карту без загрузки полного текста файлов."""
        summary = func.jsonb_build_object(
            "kind",
            F.c.analysis["kind"],
            "partial",
            F.c.analysis["partial"],
            "symbols",
            func.jsonb_path_query_array(F.c.analysis, cast("$.symbols[0 to 20]", JSONPATH)),
            "imports",
            func.jsonb_path_query_array(F.c.analysis, cast("$.imports[0 to 20]", JSONPATH)),
        )
        return await self._many(
            self._file_statement(include_text=False, include_analysis=False).add_columns(summary.label("analysis"))
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

    async def recorded_snapshots(
        self, *, connection_id: int, scope_hash: str, version: str, offset: int, limit: int
    ) -> list[dict]:
        """Возвращает страницу метаданных, исключая manifest и содержимое файлов."""
        chosen = (
            select(A)
            .where(A.c.snapshot_id == S.c.id, or_(A.c.version == version, A.c.state == "published"))
            .order_by(
                (A.c.state == "published").desc(), (A.c.version == version).desc(), A.c.id.desc()
            )
            .limit(1)
            .lateral()
        )
        return await self._many(
            select(
                S.c.id,
                S.c.connection_id,
                S.c.commit_sha,
                S.c.complete,
                S.c.coverage,
                chosen.c.state,
                chosen.c.version,
                chosen.c.coverage.label("analysis_coverage"),
            )
            .select_from(S.outerjoin(chosen, true()))
            .where(S.c.connection_id == connection_id, S.c.scope_hash == scope_hash)
            .order_by(S.c.id.desc())
            .offset(offset)
            .limit(limit)
        )

    async def snapshot_metadata(
        self, *, connection_id: int, sha: str, scope_hash: str
    ) -> dict | None:
        return await self._one(
            select(
                S.c.id,
                S.c.commit_sha,
                S.c.coverage,
                S.c.complete,
                S.c.tree_loaded,
                S.c.tree_truncated,
            ).where(
                S.c.connection_id == connection_id,
                S.c.commit_sha == sha,
                S.c.scope_hash == scope_hash,
            )
        )

    async def directory_children(
        self, *, snapshot_id: int, prefix: str, offset: int, limit: int
    ) -> list[dict]:
        """Сводит manifest в прямых детей на стороне PostgreSQL, включая старые снимки."""
        statement = text("""
            WITH paths AS (
                SELECT entry, substr(entry->>'path', length(:prefix) + 1) AS relative
                FROM git_snapshots s CROSS JOIN LATERAL jsonb_array_elements(s.manifest) AS entry
                WHERE s.id = :snapshot_id AND starts_with(entry->>'path', :prefix)
            ), children AS (
                SELECT split_part(relative, '/', 1) AS name,
                       bool_or(strpos(relative, '/') > 0) AS directory,
                       (jsonb_agg(entry) FILTER (WHERE strpos(relative, '/') = 0))->0 AS leaf
                FROM paths WHERE relative <> '' GROUP BY split_part(relative, '/', 1)
            )
            SELECT name, directory, leaf, count(*) OVER () AS total FROM children
            ORDER BY directory DESC, name COLLATE "C" LIMIT :limit OFFSET :offset
        """).bindparams(
            snapshot_id=snapshot_id,
            prefix=prefix + "/" if prefix else "",
            offset=offset,
            limit=limit,
        )
        return await self._many(statement)

    async def analysis(self, *, snapshot_id: int, version: str) -> dict | None:
        return await self._one(
            select(A).where(A.c.snapshot_id == snapshot_id, A.c.version == version)
        )

    async def published_analysis(self, *, snapshot_id: int, version: str) -> dict | None:
        return await self._one(
            select(A)
            .where(A.c.snapshot_id == snapshot_id, A.c.state == "published")
            .order_by((A.c.version == version).desc(), A.c.id.desc())
            .limit(1)
        )

    async def import_targets(
        self, *, analysis_id: int, paths: list[str], name: str | None
    ) -> list[dict]:
        parents = Y.alias("parents")
        roots = select(Y.c.id, Y.c.path).where(
            Y.c.analysis_id == analysis_id, Y.c.path.in_(paths), Y.c.parent_id.is_(None)
        )
        if name and name != "*":
            matches = (
                select(Y.c.id, Y.c.path)
                .join(parents, Y.c.parent_id == parents.c.id)
                .where(
                    Y.c.analysis_id == analysis_id,
                    Y.c.path.in_(paths),
                    Y.c.name == name,
                    parents.c.parent_id.is_(None),
                )
            )
            declared = matches.subquery()
            roots = roots.where(~exists(select(1).where(declared.c.path == Y.c.path)))
            roots = roots.union_all(matches)
        return await self._many(roots.limit(2))

    async def start_analysis(self, *, snapshot_id: int, version: str) -> dict:
        return await self._one(
            insert(A).values(snapshot_id=snapshot_id, version=version).returning(A)
        )

    async def advance_analysis(self, *, analysis_id: int, data: dict) -> None:
        await self._execute(
            update(A).where(A.c.id == analysis_id, A.c.state != "published").values(**data)
        )

    async def analysis_files(self, *, snapshot_id: int, cursor: int, limit: int) -> list[dict]:
        return await self._many(
            select(F.c.id, F.c.path, F.c.analysis, F.c.text)
            .where(F.c.snapshot_id == snapshot_id, F.c.state == "ready", F.c.id > cursor)
            .order_by(F.c.id)
            .limit(limit)
        )

    async def insert_symbols(self, *, rows: list[dict]) -> list[dict]:
        return await self._many(insert(Y).values(rows).returning(Y.c.id, Y.c.key_hash))

    async def set_symbol_parents(self, *, analysis_id: int, parents: dict[int, int]) -> None:
        await self._execute(
            update(Y)
            .where(Y.c.analysis_id == analysis_id, Y.c.id.in_(parents))
            .values(parent_id=sa_case(parents, value=Y.c.id))
        )

    async def insert_relations(self, *, rows: list[dict]) -> None:
        await self._execute(insert(E).values(rows))

    async def pending_relations(self, *, analysis_id: int, cursor: int, limit: int) -> list[dict]:
        return await self._many(
            select(E)
            .where(
                E.c.analysis_id == analysis_id,
                E.c.id > cursor,
                E.c.target_id.is_(None),
                func.jsonb_array_length(E.c.candidates) > 0,
            )
            .order_by(E.c.id)
            .limit(limit)
        )

    async def resolve_relation(self, *, analysis_id: int, edge_id: int, target_id: int) -> None:
        await self._execute(
            update(E)
            .where(E.c.analysis_id == analysis_id, E.c.id == edge_id)
            .values(target_id=target_id, confidence="resolved")
        )

    async def mark_ambiguous(self, *, analysis_id: int, edge_id: int) -> None:
        await self._execute(
            update(E)
            .where(E.c.analysis_id == analysis_id, E.c.id == edge_id)
            .values(confidence="candidate")
        )

    async def count_structure(self, *, analysis_id: int) -> dict:
        symbols = (
            select(func.count())
            .select_from(Y)
            .where(Y.c.analysis_id == analysis_id)
            .scalar_subquery()
        )
        return await self._one(
            select(
                symbols.label("symbols"),
                func.count().label("relations"),
                func.count().filter(E.c.target_id.is_(None)).label("unresolved"),
            )
            .select_from(E)
            .where(E.c.analysis_id == analysis_id)
        )

    async def structure_symbols(
        self,
        *,
        analysis_id: int,
        offset: int = 0,
        limit: int = 100,
        ids: set[int] | None = None,
        paths: list[str] | None = None,
        query: str | None = None,
        roots: bool = False,
    ) -> list[dict]:
        statement = (
            select(Y, F.c.id.label("file_id"), F.c.blob_sha, F.c.text_hash)
            .join(F, and_(Y.c.snapshot_id == F.c.snapshot_id, Y.c.path == F.c.path))
            .where(Y.c.analysis_id == analysis_id)
        )
        if ids is not None:
            statement = statement.where(Y.c.id.in_(ids))
        if paths is not None:
            statement = statement.where(Y.c.path.in_(paths))
        if roots:
            statement = statement.where(Y.c.parent_id.is_(None))
        if query:
            normalized = "".join(ch for ch in query.lower() if ch.isalnum())[:256]
            statement = statement.where(
                or_(
                    Y.c.qualified_name == query,
                    Y.c.name == query,
                    Y.c.search_name.startswith(normalized, autoescape=True)
                    if normalized
                    else False,
                )
            )
            statement = statement.order_by(
                sa_case((Y.c.qualified_name == query, 0), (Y.c.name == query, 1), else_=2)
            )
        return await self._many(
            statement.order_by(Y.c.path, Y.c.offset, Y.c.id).offset(offset).limit(limit)
        )

    async def graph_edges(
        self,
        *,
        analysis_id: int,
        frontier: set[int],
        direction: str,
        relation: str,
        confirmed: bool,
        limit: int,
        exclude: set[int],
    ) -> list[dict]:
        conditions = []
        if direction in {"in", "both"}:
            conditions.append(E.c.target_id.in_(frontier))
        if direction in {"out", "both"}:
            conditions.append(E.c.source_id.in_(frontier))
        statement = (
            select(E, Y.c.path)
            .join(Y, Y.c.id == E.c.source_id)
            .where(E.c.analysis_id == analysis_id, or_(*conditions), E.c.id.not_in(exclude))
        )
        if relation != "all":
            statement = statement.where(
                E.c.kind == "contains" if relation == "containment" else E.c.kind != "contains"
            )
        if confirmed:
            statement = statement.where(
                E.c.confidence.in_(("resolved", "declared")), E.c.target_id.is_not(None)
            )
        return await self._many(statement.order_by(E.c.id).limit(limit))

    async def snapshot_changes(
        self, *, project_id: int, connection_id: int, sha: str, limit: int
    ) -> list[dict]:
        return await self._many(
            select(G, L.c.id.label("link_id"), L.c.task_id)
            .join(L, L.c.change_id == G.c.id)
            .join(T, T.c.id == L.c.task_id)
            .where(
                T.c.project_id == project_id,
                G.c.connection_id == connection_id,
                or_(G.c.head_sha == sha, G.c.merge_sha == sha),
            )
            .order_by(G.c.id, L.c.id)
            .limit(limit)
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
        values = {
            "next_refresh_at": func.now(),
            "refresh_state": {},
            "revision": G.c.revision + 1,
        }
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
