"""Документы, связи и файлы через доменные сценарии проекта."""

import json
from pathlib import PurePath
from uuid import uuid4, uuid5

from src.agent.tools import AgentToolContext
from src.exceptions.agent_tools import AgentToolAccessError, AgentToolConflictError
from src.exceptions.storage import TaskAttachmentStorageError
from src.exceptions.task_attachments import TaskAttachmentsServiceError
from src.schemas import project_tool_inputs as inputs
from src.schemas.document_catalog import DocumentCreateRequestSchema, DocumentUpdateRequestSchema
from src.schemas.document_references import DocumentFragmentSchema
from src.services.agent_action_helpers import checked_task
from src.services.agent_files import uploaded_content
from src.services.agent_tool_scope import AgentProjectToolScope
from src.storage.agent_tool_files import read_local_file
from src.utils.document_refs import decode_document_ref


def _catalog(db: AgentProjectToolScope):
    if db.document_catalog is None:
        raise AgentToolConflictError("Каталог документов недоступен.")
    return db.document_catalog


def _source_id(document_ref: str) -> str:
    kind, entity_id = decode_document_ref(document_ref)
    return f"{kind}:{entity_id}"


async def _checked_document(db: AgentProjectToolScope, ctx: AgentToolContext, document_ref: str, *, for_write: bool = False):
    """Общий сервис проверяет доступ, инструмент дополнительно удерживает проект чата."""
    item = await _catalog(db).get(document_ref=document_ref, user_id=ctx.user_id, principal=ctx.principal, for_write=for_write)
    if item.ownership.project_id != ctx.project_id:
        raise AgentToolAccessError("Документ находится вне проекта разговора.")
    return item


def _document_result(item) -> dict:
    ref = item.ref
    return {"document": item.model_dump(mode="json"), "document_ref": ref, "source_id": _source_id(ref)}


def _request_id(ctx, operation, args):
    namespace = ctx.request_id or ctx.run_id
    value = json.dumps(args.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return uuid5(namespace, f"{operation}:{value}") if namespace else uuid4()


async def attach_uploaded_file(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.UploadedAttachmentInput
) -> dict:
    """Прикрепляет к задаче копию отправленного участником файла."""
    await checked_task(db, ctx, args.task_id)
    metadata, content = await uploaded_content(db, file_id=args.file_id, user_id=ctx.user_id)
    item = await _catalog(db).upload(
        user_id=ctx.user_id, owner_task_id=args.task_id,
        request_id=_request_id(ctx, "attach_uploaded_file", args), principal=ctx.principal,
        file_name=metadata.original_name,
        content_type=metadata.content_type,
        content=content,
    )
    return _document_result(item)


async def list_documents(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.DocumentListInput
) -> dict:
    """Читает страницу метаданных документов."""
    if args.task_id is not None:
        await checked_task(db, ctx, args.task_id)
    result = await _catalog(db).list(
        user_id=ctx.user_id, project_id=ctx.project_id, principal=ctx.principal,
        personal_only=ctx.project_id is None, **args.model_dump()
    )
    value = result.model_dump(mode="json")
    for item in value["items"]:
        item["source_id"] = _source_id(item["ref"])
        item["document_ref"] = item["ref"]
    return value


async def get_document(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.DocumentReadInput
) -> dict:
    """Возвращает страницу оригинала и связи; весь документ не раздувает контекст."""
    document = await _checked_document(db, ctx, args.document_ref)
    page = await _catalog(db).read_text(
        document_ref=args.document_ref, user_id=ctx.user_id,
        offset=args.offset, max_chars=args.max_chars, principal=ctx.principal,
    )
    result = {
        "document": document.model_dump(mode="json"),
        **page.model_dump(mode="json"),
        "source_id": _source_id(args.document_ref),
        "document_ref": args.document_ref,
    }
    if page.text:
        quote = page.text[:800]
        result["reference"] = DocumentFragmentSchema(
            document_ref=args.document_ref, rendition=page.rendition,
            content_hash=page.content_hash, start=page.offset,
            end=page.offset + len(quote), quote=quote,
        ).model_dump(mode="json")
    return result


async def create_document(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.DocumentCreateInput
) -> dict:
    """Создаёт документ в текущем проекте."""
    if args.owner_task_id is not None:
        await checked_task(db, ctx, args.owner_task_id)
    elif ctx.project_id is None:
        raise AgentToolConflictError("Для личного документа укажите owner_task_id личной задачи.")
    data = DocumentCreateRequestSchema(
        request_id=_request_id(ctx, "create_document", args),
        project_id=ctx.project_id if args.owner_task_id is None else None,
        **args.model_dump(),
    )
    document = await _catalog(db).create(user_id=ctx.user_id, data=data, principal=ctx.principal)
    return _document_result(document)


async def update_document(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.DocumentUpdateInput
) -> dict:
    """Обновляет содержимое или название существующего документа."""
    await _checked_document(db, ctx, args.document_ref, for_write=True)
    document = await _catalog(db).update(
        document_ref=args.document_ref, user_id=ctx.user_id,
        data=DocumentUpdateRequestSchema(**args.changes.model_dump(exclude_unset=True)),
        principal=ctx.principal,
    )
    return _document_result(document)


async def delete_document(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.DocumentInput
) -> dict:
    """Удаляет документ текущего проекта."""
    document = await _checked_document(db, ctx, args.document_ref, for_write=True)
    await _catalog(db).delete(document_ref=args.document_ref, user_id=ctx.user_id, principal=ctx.principal)
    return {"deleted": True, "document_ref": args.document_ref, "ownership": document.ownership.model_dump(mode="json")}


async def link_document(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.DocumentLinkInput
) -> dict:
    """Связывает документ с задачей того же проекта."""
    await _checked_document(db, ctx, args.document_ref, for_write=True)
    await checked_task(db, ctx, args.task_id)
    document = await _catalog(db).link(document_ref=args.document_ref, task_id=args.task_id, user_id=ctx.user_id, principal=ctx.principal)
    return {"linked": True, "task_id": args.task_id, **_document_result(document)}


async def unlink_document(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.DocumentLinkInput
) -> dict:
    """Удаляет связь, проверенную в границах текущего проекта."""
    await _checked_document(db, ctx, args.document_ref, for_write=True)
    await checked_task(db, ctx, args.task_id)
    document = await _catalog(db).unlink(document_ref=args.document_ref, task_id=args.task_id, user_id=ctx.user_id, principal=ctx.principal)
    return {"detached": True, "task_id": args.task_id, **_document_result(document)}


async def copy_document(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.DocumentCopyInput
) -> dict:
    """Создаёт независимую копию, читая оригинал с его текущими owner scopes."""
    original = await _checked_document(db, ctx, args.document_ref)
    await checked_task(db, ctx, args.target_task_id)
    request_id = _request_id(ctx, "copy_document", args)
    if original.source_kind == "text":
        parts = []
        offset = 0
        while offset is not None:
            page = await _catalog(db).read_text(document_ref=args.document_ref, user_id=ctx.user_id, principal=ctx.principal, offset=offset, max_chars=8000)
            if page.content_hash != original.text.content_hash:
                raise AgentToolConflictError("Документ изменился во время копирования.")
            parts.append(page.text)
            if page.next_offset is not None and page.next_offset <= offset:
                raise AgentToolConflictError("Не удалось прочитать весь документ.")
            offset = page.next_offset
        current = await _checked_document(db, ctx, args.document_ref)
        if current.text.content_hash != original.text.content_hash:
            raise AgentToolConflictError("Документ изменился во время копирования.")
        item = await _catalog(db).create(user_id=ctx.user_id, principal=ctx.principal, data=DocumentCreateRequestSchema(
            request_id=request_id, owner_task_id=args.target_task_id, title=original.title,
            caption=original.caption, content_md="".join(parts),
        ))
        return _document_result(item)
    source = await _catalog(db).prepare_download(document_ref=args.document_ref, user_id=ctx.user_id, principal=ctx.principal)
    try:
        content = await read_local_file(source.path, limit=_catalog(db).max_file_size)
    except TaskAttachmentStorageError as error:
        raise TaskAttachmentsServiceError(str(error)) from error
    await _checked_document(db, ctx, args.document_ref)
    item = await _catalog(db).upload(
        user_id=ctx.user_id, owner_task_id=args.target_task_id,
        request_id=request_id, principal=ctx.principal,
        file_name=original.original.filename,
        content_type=original.original.content_type,
        content=content,
        caption=original.caption,
        title=original.title,
    )
    return _document_result(item)


async def create_text_attachment(
    db: AgentProjectToolScope, ctx: AgentToolContext, args: inputs.TextAttachmentInput
) -> dict:
    """Прикрепляет сформированный текстовый файл без доступа модели к путям ОС."""
    await checked_task(db, ctx, args.task_id)
    if PurePath(args.file_name).suffix.lower() not in {".txt", ".md", ".csv", ".log"}:
        raise AgentToolConflictError("Для текстового файла используйте .txt, .md, .csv или .log.")
    item = await _catalog(db).upload(
        user_id=ctx.user_id, owner_task_id=args.task_id,
        request_id=_request_id(ctx, "create_text_attachment", args), principal=ctx.principal,
        file_name=args.file_name,
        content_type="text/plain",
        content=args.content.encode("utf-8"),
    )
    return _document_result(item)
