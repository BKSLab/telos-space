"""Один проектный SQL-срез и FTS по тому же реестру источников."""

from collections import defaultdict

from sqlalchemy import (
    Integer,
    Text,
    case,
    cast,
    func,
    literal,
    select,
    true,
    union_all,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, REGCONFIG, insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Base
from src.db.models.documents import Document
from src.db.models.file_search_chunks import FileSearchChunk
from src.db.models.knowledge_attachment_texts import KnowledgeAttachmentText
from src.db.models.knowledge_project_file_texts import KnowledgeProjectFileText
from src.db.models.project_files import ProjectFile
from src.db.models.task_attachments import TaskAttachment
from src.db.models.tasks import Task
from src.exceptions.knowledge import KnowledgeIndexJobsRepositoryError
from src.knowledge.catalog import POLICIES, SourcePolicy, SourceType


def _select_rows(rule: SourcePolicy, project_id: int, *, include_deleted: bool = False):
    """Строит SELECT только по явно разрешённым полям и путям принадлежности."""
    tables = Base.metadata.tables
    table = tables[rule.table]
    from_clause = table
    if rule.scope == "self":
        condition = table.c.id == project_id
    elif rule.scope == "project":
        condition = table.c.project_id == project_id
    elif rule.scope == "task":
        tasks = tables["tasks"]
        from_clause = table.join(tasks, table.c.task_id == tasks.c.id)
        condition = tasks.c.project_id == project_id
    elif rule.scope == "user":
        members = tables["project_members"]
        from_clause = table.join(members, members.c.user_id == table.c.id)
        condition = members.c.project_id == project_id
    elif rule.scope == "project_file":
        files = tables["project_files"]
        from_clause = table.join(files, table.c.project_file_id == files.c.id)
        condition = (files.c.project_id == project_id) & files.c.deleted_at.is_(None)
    else:
        attachments, tasks = tables["task_attachments"], tables["tasks"]
        from_clause = table.join(attachments, table.c.attachment_id == attachments.c.id).join(
            tasks, attachments.c.task_id == tasks.c.id
        )
        condition = tasks.c.project_id == project_id
    fields = [part for name in rule.fields for part in (literal(name), table.c[name])]
    if rule.table == "tasks" or rule.scope == "task":
        projects = tables["projects"]
        tasks = tables["tasks"]
        number = table.c.number if rule.table == "tasks" else tasks.c.number
        project_key = select(projects.c.key).where(projects.c.id == project_id).scalar_subquery()
        fields.extend([literal("task_key"), func.concat(project_key, "-", number)])
    if rule.table == "project_members":
        users = tables["users"]
        from_clause = from_clause.join(users, table.c.user_id == users.c.id)
        for name in ("username", "first_name", "last_name", "middle_name"):
            fields.extend([literal(name), users.c[name]])
    if rule.table in {"task_attachments", "project_files"}:
        is_project_file = rule.table == "project_files"
        cache = tables[
            "knowledge_project_file_texts" if is_project_file else "knowledge_attachment_texts"
        ]
        cache_id = cache.c.project_file_id if is_project_file else cache.c.attachment_id
        from_clause = from_clause.outerjoin(cache, table.c.id == cache_id)
        fields.extend([literal("extracted_text"), cache.c.text])
        if is_project_file and not include_deleted:
            condition = condition & table.c.deleted_at.is_(None)
    return (
        select(
            literal(rule.table).label("table_name"),
            literal(rule.kind.value if rule.kind else "").label("entity_type"),
            cast(func.jsonb_build_object(*fields), JSONB).label("data"),
        )
        .select_from(from_clause)
        .where(condition)
    )


class KnowledgeSourcesRepository:
    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    async def get_project_rows(self, project_id: int) -> dict[str, list[dict]]:
        """Один statement даёт согласованный снимок, включая связи и пустые разделы."""
        try:
            rows = await self.db_session.execute(
                union_all(
                    *[_select_rows(rule, project_id, include_deleted=True) for rule in POLICIES]
                )
            )
            result = defaultdict(list)
            for table, _, data in rows:
                result[table].append(data)
            return dict(result)
        except SQLAlchemyError as error:
            raise KnowledgeIndexJobsRepositoryError(
                "Не удалось прочитать источники проекта."
            ) from error

    async def search(
        self,
        project_id: int,
        query: str,
        *,
        entity_type: SourceType | None = None,
        limit: int = 30,
        offset: int = 0,
    ) -> list[dict]:
        """FTS всех источников, независимо от доступности embeddings и Qdrant.

        Проектные FK ограничивают объём до построения tsvector. Извлечённый
        текст файлов хранится локально и участвует в этом же поиске.
        """
        rules = [
            rule
            for rule in POLICIES
            if rule.kind
            and (entity_type is None or rule.kind == entity_type)
            and rule.kind
            not in {SourceType.ATTACHMENT, SourceType.PROJECT_FILE, SourceType.DOCUMENT}
        ]
        language = cast(literal("russian"), REGCONFIG)
        terms = func.websearch_to_tsquery(language, query)
        branches = []
        if rules:
            sources = union_all(*[_select_rows(rule, project_id) for rule in rules]).subquery()
            searchable = sources.c.data.op("-")(literal("storage_key"))
            search_text = cast(searchable, Text)
            parts = (
                func.generate_series(0, cast(func.floor(func.length(search_text) / 16000), Integer))
                .table_valued("part")
                .render_derived()
                .lateral()
            )
            vector = func.to_tsvector(
                language, func.substr(search_text, parts.c.part * 16000 + 1, 18048)
            )
            entity_id = sources.c.data["id"].astext
            branches.append(
                select(
                    sources.c.entity_type,
                    entity_id.label("entity_id"),
                    func.ts_rank_cd(vector, terms).label("rank"),
                )
                .select_from(sources.join(parts, true()))
                .where(vector.op("@@")(terms))
            )
        chunks = FileSearchChunk
        if entity_type in {None, SourceType.ATTACHMENT}:
            branches.append(
                select(
                    literal(SourceType.ATTACHMENT.value).label("entity_type"),
                    cast(TaskAttachment.id, Text).label("entity_id"),
                    func.ts_rank_cd(chunks.search_vector, terms).label("rank"),
                )
                .select_from(chunks)
                .join(TaskAttachment, TaskAttachment.id == chunks.attachment_id)
                .join(Task, Task.id == TaskAttachment.task_id)
                .where(Task.project_id == project_id, chunks.search_vector.op("@@")(terms))
            )
        if entity_type in {None, SourceType.PROJECT_FILE}:
            branches.append(
                select(
                    literal(SourceType.PROJECT_FILE.value).label("entity_type"),
                    cast(ProjectFile.id, Text).label("entity_id"),
                    func.ts_rank_cd(chunks.search_vector, terms).label("rank"),
                )
                .select_from(chunks)
                .join(ProjectFile, ProjectFile.id == chunks.project_file_id)
                .where(
                    ProjectFile.project_id == project_id,
                    ProjectFile.deleted_at.is_(None),
                    chunks.search_vector.op("@@")(terms),
                )
            )
        if entity_type in {None, SourceType.DOCUMENT}:
            branches.append(
                select(
                    literal(SourceType.DOCUMENT.value).label("entity_type"),
                    cast(Document.id, Text).label("entity_id"),
                    func.ts_rank_cd(Document.search_vector, terms).label("rank"),
                ).where(Document.project_id == project_id, Document.search_vector.op("@@")(terms))
            )
        matched = union_all(*branches).subquery("matched_knowledge_sources")
        rank = func.max(matched.c.rank)
        try:
            rows = await self.db_session.execute(
                select(matched.c.entity_type, matched.c.entity_id, rank.label("rank"))
                .group_by(matched.c.entity_type, matched.c.entity_id)
                .order_by(rank.desc(), matched.c.entity_type, matched.c.entity_id)
                .offset(offset)
                .limit(limit)
            )
            return [
                {"source_id": f"{kind}:{id_}", "rank": float(score)} for kind, id_, score in rows
            ]
        except SQLAlchemyError as error:
            raise KnowledgeIndexJobsRepositoryError(
                "Не удалось выполнить поиск по источникам проекта."
            ) from error

    async def save_extraction(self, data: dict) -> None:
        """Сохраняет результат файла; транзакцией управляет владелец DB-области."""
        statement = insert(KnowledgeAttachmentText).values(**data)
        try:
            await self.db_session.execute(
                statement.on_conflict_do_update(
                    index_elements=[KnowledgeAttachmentText.attachment_id],
                    set_={**data, "updated_at": func.now()},
                )
            )
        except SQLAlchemyError as error:
            raise KnowledgeIndexJobsRepositoryError(
                "Не удалось сохранить результат извлечения файла."
            ) from error

    async def start_project_files(self, *, project_id: int, embedding_signature: str) -> None:
        """Отмечает работу worker без повторного задания outbox.

        Args:
            project_id: Проект текущего задания.
            embedding_signature: Отпечаток модели и размерности векторов.
        Raises:
            KnowledgeIndexJobsRepositoryError: Прогресс не удалось записать.
        """
        cached = KnowledgeProjectFileText
        statement = insert(cached).from_select(
            ["project_file_id", "text", "status", "original_chars", "content_hash", "index_status"],
            select(
                ProjectFile.id,
                literal(""),
                literal("pending"),
                literal(0),
                ProjectFile.content_hash,
                literal("processing"),
            ).where(ProjectFile.project_id == project_id, ProjectFile.deleted_at.is_(None)),
            include_defaults=False,
        )
        try:
            await self.db_session.execute(
                statement.on_conflict_do_update(
                    index_elements=[cached.project_file_id],
                    set_={
                        "status": cached.status,
                        "index_status": case(
                            (
                                (cached.indexed_hash == statement.excluded.content_hash)
                                & (cached.embedding_signature == embedding_signature)
                                & (cached.index_status == "ready"),
                                "ready",
                            ),
                            else_="processing",
                        ),
                        "updated_at": func.now(),
                    },
                )
            )
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise KnowledgeIndexJobsRepositoryError(
                "Не удалось отметить обработку файлов проекта."
            ) from error

    async def save_project_file_extraction(self, data: dict) -> None:
        """Записывает результат только для активного оригинала той же версии.

        Args:
            data: Извлечённый текст, состояние, file ID и хэш оригинала.
        Raises:
            KnowledgeIndexJobsRepositoryError: Результат не удалось записать.
        """
        data = {"index_status": "processing", **data}
        cached = KnowledgeProjectFileText
        columns = list(data)
        statement = insert(cached).from_select(
            columns,
            select(*(literal(data[key]) for key in columns))
            .select_from(ProjectFile)
            .where(
                ProjectFile.id == data["project_file_id"],
                ProjectFile.deleted_at.is_(None),
                ProjectFile.content_hash == data["content_hash"],
            ),
            include_defaults=False,
        )
        try:
            await self.db_session.execute(
                statement.on_conflict_do_update(
                    index_elements=[cached.project_file_id], set_={**data, "updated_at": func.now()}
                )
            )
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise KnowledgeIndexJobsRepositoryError(
                "Не удалось сохранить текст файла проекта."
            ) from error

    async def reset_project_file(self, *, file_id: int) -> None:
        """Сохраняет удачный разбор при повторе незавершённых этапов.

        Args:
            file_id: Уже проверенный сервисом активный файл проекта.
        Raises:
            KnowledgeIndexJobsRepositoryError: Повтор не удалось подготовить.
        """
        cached = KnowledgeProjectFileText
        try:
            await self.db_session.execute(
                update(cached)
                .where(
                    cached.project_file_id == file_id,
                    select(ProjectFile.id)
                    .where(ProjectFile.id == file_id, ProjectFile.deleted_at.is_(None))
                    .exists(),
                )
                .values(
                    status=case((cached.status == "ready", "ready"), else_="pending"),
                    detail=None,
                    index_status="pending",
                    index_detail=None,
                )
            )
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise KnowledgeIndexJobsRepositoryError(
                "Не удалось подготовить повтор обработки файла."
            ) from error

    async def finish_project_file(
        self,
        *,
        file_id: int,
        content_hash: str,
        embedding_signature: str,
        indexed: bool,
        detail: str | None,
    ) -> None:
        """Фиксирует состояние только активной версии оригинала.

        Args:
            file_id: Исходный файл проекта.
            content_hash: Хэш оригинала из снимка задания.
            embedding_signature: Версия модели и размерности векторов.
            indexed: Записан ли производный индекс в Qdrant.
            detail: Безопасное пояснение незавершённой индексации.
        Raises:
            KnowledgeIndexJobsRepositoryError: Состояние не удалось записать.
        """
        cached = KnowledgeProjectFileText
        ready = (cached.status == "ready") & literal(indexed)
        active = (
            select(ProjectFile.id)
            .where(
                ProjectFile.id == file_id,
                ProjectFile.deleted_at.is_(None),
                ProjectFile.content_hash == content_hash,
            )
            .exists()
        )
        try:
            await self.db_session.execute(
                update(cached)
                .where(
                    cached.project_file_id == file_id, cached.content_hash == content_hash, active
                )
                .values(
                    index_status=case(
                        (ready, "ready"),
                        (cached.status.in_(["pending", "processing"]), "pending"),
                        (cached.status.in_(["empty", "unsupported", "disabled"]), "skipped"),
                        else_="failed",
                    ),
                    index_detail=case(
                        (ready, None), else_=detail or "Текст файла пока не проиндексирован."
                    ),
                    indexed_hash=case((ready, content_hash), else_=cached.indexed_hash),
                    embedding_signature=case(
                        (ready, embedding_signature), else_=cached.embedding_signature
                    ),
                    indexed_at=case((ready, func.now()), else_=cached.indexed_at),
                )
            )
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise KnowledgeIndexJobsRepositoryError(
                "Не удалось сохранить состояние индекса файла."
            ) from error
