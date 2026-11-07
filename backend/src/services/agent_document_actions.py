"""Документы, связи и файлы через доменные сценарии проекта."""

from pathlib import PurePath

from src.agent.tools import AgentToolContext
from src.exceptions.agent_tools import AgentToolAccessError, AgentToolConflictError
from src.exceptions.storage import TaskAttachmentStorageError
from src.exceptions.task_attachments import TaskAttachmentsServiceError
from src.schemas import project_tool_inputs as inputs
from src.services.agent_action_helpers import checked_document, checked_task, page
from src.services.agent_files import uploaded_content
from src.services.agent_tool_scope import AgentProjectToolScope
from src.services.task_attachments import TaskAttachmentsService
from src.storage.agent_tool_files import read_local_file


async def attach_uploaded_file(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.UploadedAttachmentInput
) -> dict:
    """Прикрепляет к задаче копию отправленного участником файла."""
    await checked_task(db, ctx, args.task_id)
    metadata, content = await uploaded_content(db, file_id=args.file_id, user_id=ctx.user_id)
    item = await db.attachments.upload_attachment(
        task_id=args.task_id,
        file_name=metadata.original_name,
        content_type=metadata.content_type,
        content=content,
        uploaded_by_user_id=ctx.user_id,
    )
    return {"attachment": item.model_dump(mode="json"), "source_id": f"attachment:{item.id}"}


async def list_documents(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.PageInput
) -> dict:
    """Читает страницу метаданных документов."""
    return page(await db.documents.get_document_list(ctx.project_id), args)


async def get_document(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.DocumentReadInput
) -> dict:
    """Возвращает страницу оригинала и связи; весь документ не раздувает контекст."""
    await checked_document(db, ctx, args.document_id)
    document = await db.documents.get_document(args.document_id)
    text = document.content_md or ""
    end = min(len(text), args.offset + args.max_chars)
    return {
        "document": {
            **document.model_dump(mode="json", exclude={"content_md"}),
            "content_md": text[args.offset : end],
            "offset": args.offset,
            "total_chars": len(text),
            "next_offset": end if end < len(text) else None,
        },
        "tasks": [
            item.model_dump(mode="json")
            for item in await db.links.get_links_for_document(args.document_id)
        ],
        "source_id": f"document:{document.id}",
    }


async def create_document(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.DocumentCreateInput
) -> dict:
    """Создаёт документ в текущем проекте."""
    document = await db.documents.create_document(
        ctx.project_id, args.title, args.slug, args.content_md
    )
    return {"document": document.model_dump(mode="json"), "source_id": f"document:{document.id}"}


async def update_document(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.DocumentUpdateInput
) -> dict:
    """Обновляет содержимое или название существующего документа."""
    await checked_document(db, ctx, args.document_id)
    document = await db.documents.update_document(
        args.document_id, args.changes.model_dump(exclude_unset=True)
    )
    return {"document": document.model_dump(mode="json"), "source_id": f"document:{document.id}"}


async def delete_document(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.DocumentInput
) -> dict:
    """Удаляет документ текущего проекта."""
    await checked_document(db, ctx, args.document_id)
    await db.documents.delete_document(args.document_id)
    return {"deleted": True, "document_id": args.document_id}


async def link_document(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.DocumentLinkInput
) -> dict:
    """Связывает документ с задачей того же проекта."""
    await checked_document(db, ctx, args.document_id)
    await checked_task(db, ctx, args.task_id)
    return {
        "link": (
            await db.links.create_link(args.document_id, args.task_id, ctx.user_id)
        ).model_dump(mode="json")
    }


async def unlink_document(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.LinkInput
) -> dict:
    """Удаляет связь, проверенную в границах текущего проекта."""
    grant = await db.access.ensure_link_access(link_id=args.link_id, user_id=ctx.user_id)
    if grant.project_id != ctx.project_id:
        raise AgentToolAccessError("Связь находится вне проекта разговора.")
    await db.links.delete_link(args.link_id)
    return {"deleted": True, "link_id": args.link_id}


async def list_attachments(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.TaskInput
) -> dict:
    """Возвращает метаданные и ссылки файлов доступной задачи."""
    await checked_task(db, ctx, args.task_id)
    return {
        "items": [
            item.model_dump(mode="json")
            for item in await db.attachments.get_attachments(args.task_id)
        ]
    }


async def copy_attachment(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.AttachmentCopyInput
) -> dict:
    """Прикрепляет копию существующего файла проекта к другой его задаче."""
    await checked_task(db, ctx, args.task_id)
    await checked_task(db, ctx, args.target_task_id)
    source = await db.attachments.get_attachment_content(
        task_id=args.task_id, attachment_id=args.attachment_id
    )
    try:
        content = await read_local_file(source.path, limit=TaskAttachmentsService.MAX_FILE_SIZE)
    except TaskAttachmentStorageError as error:
        raise TaskAttachmentsServiceError(str(error)) from error
    item = await db.attachments.upload_attachment(
        task_id=args.target_task_id,
        file_name=source.original_name,
        content_type=source.content_type,
        content=content,
        uploaded_by_user_id=ctx.user_id,
    )
    return {"attachment": item.model_dump(mode="json"), "source_id": f"attachment:{item.id}"}


async def create_text_attachment(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.TextAttachmentInput
) -> dict:
    """Прикрепляет сформированный текстовый файл без доступа модели к путям ОС."""
    await checked_task(db, ctx, args.task_id)
    if PurePath(args.file_name).suffix.lower() not in {".txt", ".md", ".csv", ".log"}:
        raise AgentToolConflictError("Для текстового файла используйте .txt, .md, .csv или .log.")
    item = await db.attachments.upload_attachment(
        task_id=args.task_id,
        file_name=args.file_name,
        content_type="text/plain",
        content=args.content.encode("utf-8"),
        uploaded_by_user_id=ctx.user_id,
    )
    return {"attachment": item.model_dump(mode="json"), "source_id": f"attachment:{item.id}"}


async def delete_attachment(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.AttachmentInput
) -> dict:
    """Удаляет выбранный файл существующей задачи проекта."""
    await checked_task(db, ctx, args.task_id)
    await db.attachments.delete_attachment(task_id=args.task_id, attachment_id=args.attachment_id)
    return {"deleted": True, "attachment_id": args.attachment_id}
