"""GIN-поиск по оригиналам и прямому вектору Document с актуальным ACL."""

from sqlalchemy import Integer, Text, and_, cast, func, literal, or_, select, union_all
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models.document_links import DocumentLink
from src.db.models.documents import Document
from src.db.models.file_search_chunks import FileSearchChunk
from src.db.models.project_files import ProjectFile
from src.db.models.project_members import ProjectMember
from src.db.models.projects import Project
from src.db.models.task_attachments import TaskAttachment
from src.db.models.task_participants import TaskParticipant
from src.db.models.tasks import Task
from src.exceptions.file_processing import FileSearchRepositoryError
from src.schemas.file_search import FileSearchScope
from src.utils.fts import HIGHLIGHT_END, HIGHLIGHT_START, build_ts_query

HEADLINE_OPTIONS = (
    f"StartSel={HIGHLIGHT_START}, StopSel={HIGHLIGHT_END}, MaxWords=45, MinWords=12, MaxFragments=2"
)


def _project_visible(project_id, user_id: int):
    return (
        select(ProjectMember.id)
        .where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
        .exists()
    )


def _task_visible(user_id: int):
    personal = (
        select(TaskParticipant.id)
        .where(TaskParticipant.task_id == Task.id, TaskParticipant.user_id == user_id)
        .exists()
    )
    return or_(
        _project_visible(Task.project_id, user_id), and_(Task.project_id.is_(None), personal)
    )


def file_candidates(
    *,
    query: str,
    user_id: int,
    project_id: int | None,
    task_id: int | None,
    scope: FileSearchScope = "all",
):
    """Строит индексируемые ветви; принадлежность задачи никогда не кешируется."""
    terms = build_ts_query(query)
    chunks = FileSearchChunk
    branches = []
    if scope in {"all", "project_files"} and project_id is not None and task_id is None:
        branches.append(
            select(
                literal("project_file").label("source_type"),
                ProjectFile.id.label("source_id"),
                ProjectFile.project_id,
                cast(literal(None), Integer).label("task_id"),
                ProjectFile.original_name.label("title"),
                ProjectFile.caption,
                ProjectFile.content_type,
                cast(literal(None), Text).label("task_key"),
                cast(literal(None), Text).label("task_title"),
                cast(literal(None), Text).label("document_slug"),
                chunks.chunk_index,
                func.concat(chunks.title, "\n", chunks.caption, "\n", chunks.body).label(
                    "excerpt_text"
                ),
                func.ts_rank_cd(chunks.search_vector, terms).label("rank"),
            )
            .select_from(chunks)
            .join(ProjectFile, ProjectFile.id == chunks.project_file_id)
            .where(
                ProjectFile.project_id == project_id,
                ProjectFile.deleted_at.is_(None),
                _project_visible(ProjectFile.project_id, user_id),
                chunks.search_vector.op("@@")(terms),
            )
        )
    if scope in {"all", "task_files"}:
        conditions = [_task_visible(user_id), chunks.search_vector.op("@@")(terms)]
        if project_id is not None:
            conditions.append(Task.project_id == project_id)
        if task_id is not None:
            conditions.append(Task.id == task_id)
        branches.append(
            select(
                literal("task_attachment").label("source_type"),
                TaskAttachment.id.label("source_id"),
                Task.project_id,
                Task.id.label("task_id"),
                TaskAttachment.original_name.label("title"),
                TaskAttachment.caption,
                TaskAttachment.content_type,
                func.concat(func.coalesce(Project.key, "T"), "-", Task.number).label("task_key"),
                Task.title.label("task_title"),
                cast(literal(None), Text).label("document_slug"),
                chunks.chunk_index,
                func.concat(chunks.title, "\n", chunks.caption, "\n", chunks.body).label(
                    "excerpt_text"
                ),
                func.ts_rank_cd(chunks.search_vector, terms).label("rank"),
            )
            .select_from(chunks)
            .join(TaskAttachment, TaskAttachment.id == chunks.attachment_id)
            .join(Task, Task.id == TaskAttachment.task_id)
            .outerjoin(Project, Project.id == Task.project_id)
            .where(*conditions)
        )
    if scope in {"all", "documents"}:
        conditions = [
            _project_visible(Document.project_id, user_id),
            Document.search_vector.op("@@")(terms),
        ]
        if project_id is not None:
            conditions.append(Document.project_id == project_id)
        statement = select(
            literal("document").label("source_type"),
            Document.id.label("source_id"),
            Document.project_id,
            (literal(task_id) if task_id is not None else cast(literal(None), Integer)).label(
                "task_id"
            ),
            Document.title,
            cast(literal(None), Text).label("caption"),
            cast(literal(None), Text).label("content_type"),
            (
                func.concat(func.coalesce(Project.key, "T"), "-", Task.number)
                if task_id is not None
                else cast(literal(None), Text)
            ).label("task_key"),
            (Task.title if task_id is not None else cast(literal(None), Text)).label("task_title"),
            Document.slug.label("document_slug"),
            literal(0).label("chunk_index"),
            func.concat(Document.title, "\n", Document.content_md).label("excerpt_text"),
            func.ts_rank_cd(Document.search_vector, terms).label("rank"),
        )
        if task_id is not None:
            statement = (
                statement.join(DocumentLink, DocumentLink.document_id == Document.id)
                .join(Task, Task.id == DocumentLink.task_id)
                .outerjoin(Project, Project.id == Task.project_id)
            )
            conditions.extend(
                [Task.id == task_id, Task.project_id == Document.project_id, _task_visible(user_id)]
            )
        branches.append(statement.where(*conditions))
    if not branches:
        return None
    return union_all(*branches).subquery("file_candidates")


class FileSearchRepository:
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session

    async def search(
        self,
        *,
        query: str,
        user_id: int,
        project_id: int | None,
        task_id: int | None,
        scope: FileSearchScope,
        limit: int,
        offset: int,
    ) -> dict:
        """Одним snapshot возвращает страницу лучших окон и общее число оригиналов."""
        candidates = file_candidates(
            query=query, user_id=user_id, project_id=project_id, task_id=task_id, scope=scope
        )
        if candidates is None:
            return {"items": [], "total": 0}
        ranked = select(
            *candidates.c,
            func.row_number()
            .over(
                partition_by=(candidates.c.source_type, candidates.c.source_id),
                order_by=(candidates.c.rank.desc(), candidates.c.chunk_index),
            )
            .label("position"),
        ).cte("ranked_file_windows")
        sources = (
            select(*[column for column in ranked.c if column.key != "position"])
            .where(ranked.c.position == 1)
            .cte("matched_files")
        )
        page = (
            select(sources)
            .order_by(sources.c.rank.desc(), sources.c.source_type, sources.c.source_id)
            .limit(limit)
            .offset(offset)
            .subquery("file_page")
        )
        excerpt = func.ts_headline(
            "russian", page.c.excerpt_text, build_ts_query(query), HEADLINE_OPTIONS
        )
        fields = [
            item
            for name in (
                "source_type",
                "source_id",
                "project_id",
                "task_id",
                "title",
                "caption",
                "content_type",
                "task_key",
                "task_title",
                "document_slug",
                "rank",
            )
            for item in (literal(name), page.c[name])
        ]
        fields.extend([literal("excerpt"), func.left(excerpt, 1600)])
        items = select(
            func.jsonb_agg(
                aggregate_order_by(
                    func.jsonb_build_object(*fields),
                    page.c.rank.desc(),
                    page.c.source_type,
                    page.c.source_id,
                )
            )
        ).scalar_subquery()
        statement = select(
            select(func.count()).select_from(sources).scalar_subquery().label("total"),
            func.coalesce(items, cast(literal("[]"), JSONB)).label("items"),
        )
        try:
            row = (await self.db_session.execute(statement)).mappings().one()
            return dict(row)
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise FileSearchRepositoryError(
                "Не удалось найти файлы по локальному индексу."
            ) from error
