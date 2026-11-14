"""Единая ограниченная проекция объектов для карточек и поиска ссылок."""

from sqlalchemy import String, cast, func, literal, select, union_all

from src.db.models.documents import Document
from src.db.models.project_files import ProjectFile
from src.db.models.project_milestones import ProjectMilestone
from src.db.models.project_risks import ProjectRisk
from src.db.models.project_stages import ProjectStage
from src.db.models.projects import Project
from src.db.models.task_attachments import TaskAttachment
from src.db.models.tasks import Task
from src.db.models.wbs_nodes import WbsNode
from src.repositories.chat_base import ChatRepository
from src.utils.document_refs import decode_document_ref, encode_document_ref

MODELS = {
    "TASK": Task,
    "RISK": ProjectRisk,
    "MILESTONE": ProjectMilestone,
    "WBS_NODE": WbsNode,
}


class ChatEntityLookupRepository(ChatRepository):
    """Каждый поиск — один SQL UNION с project_id в каждой ветке."""

    async def find(
        self,
        project_id: int,
        *,
        query: str = "",
        refs: list[tuple[str, int | str]] | None = None,
        limit: int = 30,
    ):
        """Возвращает только поля карточки, без содержимого документов."""
        branches = []
        for kind, model in MODELS.items():
            status = (
                ProjectStage.name
                if kind == "TASK"
                else cast(model.status, String)
                if kind in {"RISK", "MILESTONE"}
                else literal(None, String)
            )
            extra = (
                cast(Task.number, String)
                if kind == "TASK"
                else cast(ProjectMilestone.due_date, String)
                if kind == "MILESTONE"
                else literal(None, String)
            )
            statement = (
                select(
                    literal(kind).label("entity_type"),
                    model.id.label("entity_id"),
                    model.title,
                    status.label("status"),
                    extra.label("extra"),
                    Project.key.label("project_key"),
                    literal(None, String).label("document_source_kind"),
                )
                .join(Project, Project.id == model.project_id)
                .where(model.project_id == project_id)
            )
            if kind == "TASK":
                statement = statement.join(ProjectStage, ProjectStage.id == Task.stage_id)
            if query:
                statement = statement.where(model.title.icontains(query, autoescape=True))
            if refs is not None:
                statement = statement.where(
                    model.id.in_(
                        [entity_id for entity_type, entity_id in refs if entity_type == kind]
                    )
                )
            branches.append(statement)
        for source_kind, model in (("document", Document), ("project_file", ProjectFile), ("attachment", TaskAttachment)):
            title = model.title if source_kind == "document" else func.coalesce(model.display_title, model.original_name)
            statement = select(
                literal("DOCUMENT").label("entity_type"), model.id.label("entity_id"),
                title.label("title"), literal(None, String).label("status"),
                literal(None, String).label("extra"), Project.key.label("project_key"),
                literal(source_kind).label("document_source_kind"),
            ).select_from(model)
            if source_kind == "document":
                statement = statement.outerjoin(Task, Task.id == Document.owner_task_id).join(
                    Project, Project.id == func.coalesce(Document.project_id, Task.project_id)
                )
            elif source_kind == "attachment":
                statement = statement.join(Task, Task.id == TaskAttachment.task_id).join(Project, Project.id == Task.project_id)
            else:
                statement = statement.join(Project, Project.id == ProjectFile.project_id).where(ProjectFile.deleted_at.is_(None))
            statement = statement.where(Project.id == project_id)
            if query:
                statement = statement.where(title.icontains(query, autoescape=True))
            if refs is not None:
                identifiers = []
                for entity_type, reference in refs:
                    if entity_type != "DOCUMENT" or not isinstance(reference, str):
                        continue
                    try:
                        ref_kind, ref_id = decode_document_ref(reference)
                    except ValueError:
                        continue
                    if ref_kind == source_kind:
                        identifiers.append(ref_id)
                statement = statement.where(model.id.in_(identifiers))
            branches.append(statement)
        rows = list(
            (
                await self._execute(
                    union_all(*branches).order_by("title", "entity_id").limit(limit)
                )
            ).mappings()
        )
        result = []
        for row in rows:
            item = dict(row)
            source_kind = item.pop("document_source_kind")
            item["document_ref"] = encode_document_ref(source_kind, item["entity_id"]) if source_kind else None
            if source_kind:
                item["entity_id"] = None
            result.append(item)
        return result
