"""Единые документы: настоящий PostgreSQL, файлы и действующие права."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, update

from src.core.settings import get_settings
from src.db.models.api_keys import ApiKey
from src.db.models.document_index_states import DocumentIndexState
from src.db.models.document_submissions import DocumentSubmission
from src.db.models.documents import Document
from src.db.models.knowledge_attachment_texts import KnowledgeAttachmentText
from src.db.models.project_files import ProjectFile
from src.db.models.tasks import Task
from src.db.models.users import User
from src.dependencies.document_catalog import build_document_catalog_scope
from src.exceptions.api_keys import ApiKeyScopeError
from src.exceptions.auth import AuthServiceError
from src.exceptions.document_catalog import (
    CatalogDocumentConflictError,
    CatalogDocumentNotFoundError,
    CatalogDocumentValidationError,
)
from src.knowledge.catalog import digest
from src.schemas.document_catalog import DocumentCreateRequestSchema, DocumentUpdateRequestSchema
from src.services.document_catalog import DocumentCatalogService
from src.utils.api_keys import generate_api_key, hash_api_key
from src.utils.document_refs import decode_document_ref, document_text_hash, encode_document_ref
from tests.integration.repositories.test_file_processing_search import worker
from tests.integration.repositories.test_file_processing_search import world as _catalog_world
from tests.integration.repositories.test_project_file_pipeline import build_pipeline

catalog_world = _catalog_world


@pytest_asyncio.fixture
async def catalog(catalog_world):
    world = catalog_world
    settings = get_settings()
    service = DocumentCatalogService(
        scope=build_document_catalog_scope(session_factory=world.sessions, settings=settings),
        storage=world.storage,
        knowledge_enabled=False,
        max_file_size=10 * 1024 * 1024,
        embedding_signature="test-model",
    )
    yield SimpleNamespace(world=world, service=service)
    async with world.sessions() as db:
        await db.execute(delete(ApiKey).where(ApiKey.owner_user_id == world.owner))
        await db.commit()


async def create_text(env, *, task=False, content="Требования ✨😀\n\nПолный исходный текст"):
    return await env.service.create(
        user_id=env.world.owner,
        data=DocumentCreateRequestSchema(
            request_id=uuid4(),
            title="Условия поставки",
            content_md=content,
            **(
                {"owner_task_id": env.world.personal_task}
                if task
                else {"project_id": env.world.project}
            ),
        ),
    )


async def upload(env, *, task=False, request_id=None):
    return await env.service.upload(
        user_id=env.world.owner,
        request_id=request_id or uuid4(),
        file_name="Поставка.txt",
        content_type="text/plain",
        content="Кварцевые требования 😀".encode(),
        **({"owner_task_id": env.world.task} if task else {"project_id": env.world.project}),
    )


async def test_library_ownership_filters_and_full_unicode_rendition(catalog):
    env = catalog
    text = await create_text(env)
    personal = await create_text(env, task=True)
    original = await upload(env)
    attached = await upload(env, task=True)
    assert text.source_kind == "text" and original.source_kind == "upload"
    assert personal.ownership.project_id is None and personal.states.semantic.status == "disabled"
    assert attached.ownership.owner_task_id == env.world.task
    page = await env.service.list(user_id=env.world.owner, project_id=env.world.project)
    assert page.total == 3
    assert {item.ref for item in page.items} == {text.ref, original.ref, attached.ref}
    project_only = await env.service.list(
        user_id=env.world.owner, project_id=env.world.project, scope="project"
    )
    assert {item.ref for item in project_only.items} == {text.ref, original.ref}
    await env.service.link(
        document_ref=original.ref, user_id=env.world.owner, task_id=env.world.task
    )
    project_only = await env.service.list(
        user_id=env.world.owner, project_id=env.world.project, scope="project"
    )
    assert [item.ref for item in project_only.items] == [text.ref]
    task_page = await env.service.list(user_id=env.world.owner, task_id=env.world.task)
    assert {item.ref for item in task_page.items} == {original.ref, attached.ref}
    # Авторский текст не ждёт фоновой обработки; смещения — code points.
    parts = []
    offset = 0
    while True:
        part = await env.service.read_text(
            document_ref=personal.ref, user_id=env.world.owner, offset=offset, max_chars=5
        )
        assert part.content_hash == personal.text.content_hash
        parts.append(part.text)
        if part.next_offset is None:
            break
        offset = part.next_offset
    assert document_text_hash("".join(parts)) == part.content_hash
    file_worker = worker(env.world)
    while await file_worker.process_next():
        pass
    rendition = await env.service.read_text(document_ref=attached.ref, user_id=env.world.owner)
    assert rendition.text == "Кварцевые требования 😀"
    found = await env.service.list(
        user_id=env.world.owner, project_id=env.world.project, q="кварцевые"
    )
    assert {item.ref for item in found.items} == {original.ref, attached.ref}
    assert (
        await env.service.get(document_ref=attached.ref, user_id=env.world.owner)
    ).states.fts.status == "ready"
    # Примечание Markdown входит в сохраняемый GIN-вектор.
    await env.service.update(
        document_ref=text.ref,
        user_id=env.world.owner,
        data=DocumentUpdateRequestSchema(
            expected_revision=text.revision, caption="янтарнотелескопический"
        ),
    )
    by_caption = await env.service.list(
        user_id=env.world.owner, project_id=env.world.project, q="янтарнотелескопический"
    )
    assert [item.ref for item in by_caption.items] == [text.ref]


async def test_concurrent_upload_and_create_replays_do_not_duplicate(catalog):
    env = catalog
    request = uuid4()
    first, repeated = await asyncio.gather(
        upload(env, request_id=request), upload(env, request_id=request)
    )
    assert first.ref == repeated.ref
    page = await env.service.list(user_id=env.world.owner, project_id=env.world.project)
    assert page.total == 1
    assert len(list(env.world.storage.root.rglob("*.txt"))) == 1
    with pytest.raises(CatalogDocumentConflictError):
        await env.service.upload(
            user_id=env.world.owner,
            request_id=request,
            file_name="Поставка.txt",
            content_type="text/plain",
            content=b"changed",
            project_id=env.world.project,
        )
    async with env.world.sessions() as db:
        assert (await db.execute(select(func.count(Document.id)))).scalar_one() == 0
    # Совпадение хэша разных намеренных отправок не сливает их смысл и владельцев.
    distinct = await upload(env)
    assert distinct.ref != first.ref


async def test_acl_rechecked_on_replay_edit_and_stable_ref_after_task_move(catalog):
    env = catalog
    attached = await upload(env, task=True)
    text = await create_text(env)
    with pytest.raises(CatalogDocumentNotFoundError):
        await env.service.get(document_ref=attached.ref, user_id=env.world.stranger)
    updated = await env.service.update(
        document_ref=text.ref,
        user_id=env.world.owner,
        data=DocumentUpdateRequestSchema(expected_revision=text.revision, title="Изменено"),
    )
    assert updated.revision > text.revision
    with pytest.raises(CatalogDocumentConflictError):
        await env.service.update(
            document_ref=text.ref,
            user_id=env.world.owner,
            data=DocumentUpdateRequestSchema(
                expected_revision=text.revision, content_md="lost update"
            ),
        )
    async with env.world.sessions() as db:
        await db.execute(
            update(Task)
            .where(Task.id == env.world.task)
            .values(project_id=env.world.other_project, stage_id=env.world.other_stage)
        )
        await db.commit()
    with pytest.raises(CatalogDocumentNotFoundError):
        await env.service.get(document_ref=attached.ref, user_id=env.world.owner)
    moved = await env.service.get(document_ref=attached.ref, user_id=env.world.stranger)
    assert moved.ref == attached.ref and moved.ownership.project_id == env.world.other_project
    assert moved.revision > attached.revision
    assert (
        await env.service.list(user_id=env.world.owner, project_id=env.world.project)
    ).total == 1


async def test_unlink_preserves_shared_original_and_owner_is_not_a_link(catalog):
    env = catalog
    original = await upload(env)
    attached = await upload(env, task=True)
    linked = await env.service.link(
        document_ref=original.ref, user_id=env.world.owner, task_id=env.world.task
    )
    assert linked.tasks[0].relation == "linked"
    await env.service.unlink(
        document_ref=original.ref, user_id=env.world.owner, task_id=env.world.task
    )
    assert not (await env.service.get(document_ref=original.ref, user_id=env.world.owner)).tasks
    download = await env.service.prepare_download(
        document_ref=original.ref, user_id=env.world.owner
    )
    assert download.path.read_text(encoding="utf-8") == "Кварцевые требования 😀"
    with pytest.raises(CatalogDocumentValidationError):
        await env.service.unlink(
            document_ref=attached.ref, user_id=env.world.owner, task_id=env.world.task
        )
    await env.service.link(
        document_ref=original.ref, user_id=env.world.owner, task_id=env.world.task
    )
    async with env.world.sessions() as db:
        await db.execute(delete(Task).where(Task.id == env.world.task))
        await db.commit()
    assert (
        await env.service.get(document_ref=original.ref, user_id=env.world.owner)
    ).ref == original.ref
    with pytest.raises(CatalogDocumentNotFoundError):
        await env.service.get(document_ref=attached.ref, user_id=env.world.owner)


async def test_semantic_receipt_requires_actual_current_version_and_text(catalog):
    env = catalog
    source = await upload(env, task=True)
    semantic = DocumentCatalogService(
        scope=env.service.scope,
        storage=env.world.storage,
        knowledge_enabled=True,
        max_file_size=10 * 1024 * 1024,
        embedding_signature="test-model",
    )
    assert (
        await semantic.get(document_ref=source.ref, user_id=env.world.owner)
    ).states.semantic.status == "pending"
    while await worker(env.world).process_next():
        pass
    current = await semantic.get(document_ref=source.ref, user_id=env.world.owner)
    kind, identifier = decode_document_ref(source.ref)
    async with env.world.sessions() as db:
        db.add(
            DocumentIndexState(
                project_id=env.world.project,
                source_id=f"{kind}:{identifier}",
                source_hash="a" * 64,
                source_version=current.revision,
                text_hash=current.text.content_hash,
                embedding_signature="test-model",
                status="ready",
            )
        )
        await db.commit()
    assert (
        await semantic.get(document_ref=source.ref, user_id=env.world.owner)
    ).states.semantic.status == "ready"
    await semantic.update(
        document_ref=source.ref,
        user_id=env.world.owner,
        data=DocumentUpdateRequestSchema(expected_revision=current.revision, caption="Новый смысл"),
    )
    assert (
        await semantic.get(document_ref=source.ref, user_id=env.world.owner)
    ).states.semantic.status == "pending"
    async with env.world.sessions() as db:
        cached = await db.get(KnowledgeAttachmentText, identifier)
        cached.text = "Изменённое представление"
        await db.commit()
    assert (
        await semantic.get(document_ref=source.ref, user_id=env.world.owner)
    ).states.semantic.status == "pending"


async def test_all_three_document_kinds_share_worker_receipts_and_reuse_vectors(
    catalog, monkeypatch
):
    env = catalog
    text, original, attached = (
        await create_text(env),
        await upload(env),
        await upload(env, task=True),
    )
    processor = worker(env.world)
    while await processor.process_next():
        pass
    # Production индексатор использует только сохранённое представление.
    from unittest.mock import AsyncMock

    from src.knowledge.extract import extract_indexable_text

    extractor = AsyncMock(
        spec=extract_indexable_text, side_effect=AssertionError("Повторное извлечение оригинала")
    )
    monkeypatch.setattr("src.services.knowledge_index.extract_indexable_text", extractor)
    async with env.world.sessions() as db:
        pipeline_worker, indexer, _, qdrant, embeddings = build_pipeline(db, env.world.storage)
        indexer.extract_originals = False
        try:
            result = await pipeline_worker._prepare_and_execute_job(
                SimpleNamespace(id=1, project_id=env.world.project, attempts=1)
            )
            assert result.error is None
            signature = digest([embeddings.model, qdrant.vector_dim])
            semantic = DocumentCatalogService(
                scope=env.service.scope,
                storage=env.world.storage,
                knowledge_enabled=True,
                max_file_size=10 * 1024 * 1024,
                embedding_signature=signature,
            )
            for document in (text, original, attached):
                indexed = await semantic.get(document_ref=document.ref, user_id=env.world.owner)
                assert indexed.states.semantic.status == "ready"
            calls = embeddings.get_embeddings.await_count
            repeated = await pipeline_worker._prepare_and_execute_job(
                SimpleNamespace(id=2, project_id=env.world.project, attempts=1)
            )
            assert repeated.error is None
            assert embeddings.get_embeddings.await_count == calls
            extractor.assert_not_awaited()
        finally:
            await qdrant.close()


def test_document_ref_does_not_contain_mutable_context_and_has_one_spelling():
    for kind in ("document", "project_file", "attachment"):
        ref = encode_document_ref(kind, 42)
        assert decode_document_ref(ref) == (kind, 42)
        with pytest.raises(ValueError):
            decode_document_ref(ref + "=")


async def authenticated_catalog(env, scopes):
    raw, prefix = generate_api_key()
    async with env.world.sessions() as db:
        key = ApiKey(
            label="Synthetic catalogue key",
            key_hash=hash_api_key(raw),
            key_prefix=prefix,
            kind="personal",
            owner_user_id=env.world.owner,
            scopes=list(scopes),
        )
        db.add(key)
        await db.commit()
        key_id = key.id
    service = DocumentCatalogService(
        scope=env.service.scope,
        storage=env.world.storage,
        knowledge_enabled=False,
        max_file_size=10 * 1024 * 1024,
        embedding_signature="test-model",
    )
    principal = await service.resolve_principal(session_token=None, bearer_secret=raw)
    return service, principal, key_id


async def test_credential_scopes_filter_before_count_and_owner_checks_apply_to_every_action(
    catalog,
):
    env = catalog
    project_text = await create_text(env)
    task_file = await upload(env, task=True)
    personal_text = await create_text(env, task=True)
    service, principal, _ = await authenticated_catalog(env, {"tasks:read", "tasks:write"})
    page = await service.list(user_id=env.world.owner, principal=principal, limit=1)
    assert page.total == 2 and page.next_offset == 1
    assert page.items[0].ownership.kind == "task"
    projected = await service.list(
        user_id=env.world.owner, principal=principal, project_id=env.world.project
    )
    assert projected.total == 1 and projected.items[0].ref == task_file.ref
    with pytest.raises(ApiKeyScopeError):
        await service.get(
            document_ref=project_text.ref, user_id=env.world.owner, principal=principal
        )
    await service.read_text(
        document_ref=personal_text.ref, user_id=env.world.owner, principal=principal
    )
    with pytest.raises(ApiKeyScopeError):
        await service.link(
            document_ref=project_text.ref,
            task_id=env.world.task,
            user_id=env.world.owner,
            principal=principal,
        )
    # Write позволяет создать результат, но не открывает GET документа.
    write_only, grant, _ = await authenticated_catalog(env, {"projects:write"})
    request = DocumentCreateRequestSchema(
        request_id=uuid4(),
        project_id=env.world.project,
        title="Создано ключом",
        content_md="Приватный текст",
    )
    created = await write_only.create(user_id=env.world.owner, principal=grant, data=request)
    assert created.capabilities.can_edit and not created.capabilities.can_read
    assert not created.capabilities.can_link
    replay = await write_only.create(user_id=env.world.owner, principal=grant, data=request)
    assert replay.ref == created.ref
    with pytest.raises(ApiKeyScopeError):
        await write_only.get(document_ref=created.ref, user_id=env.world.owner, principal=grant)


@pytest.mark.parametrize("change", ["key_revoked", "inactive_user", "reduced_scopes"])
async def test_actual_key_and_owner_are_rechecked_after_original_io(catalog, change, monkeypatch):
    env = catalog
    service, principal, key_id = await authenticated_catalog(
        env, {"projects:read", "projects:write"}
    )
    started, release = asyncio.Event(), asyncio.Event()
    original_save = env.world.storage.save_in

    async def delayed(*args, **kwargs):
        key = await original_save(*args, **kwargs)
        started.set()
        await release.wait()
        return key

    monkeypatch.setattr(env.world.storage, "save_in", delayed)
    request_id = uuid4()
    operation = asyncio.create_task(
        service.upload(
            user_id=env.world.owner,
            principal=principal,
            request_id=request_id,
            project_id=env.world.project,
            file_name="authorization.txt",
            content_type="text/plain",
            content=b"synthetic",
        )
    )
    await started.wait()
    async with env.world.sessions() as db:
        if change == "key_revoked":
            await db.execute(update(ApiKey).where(ApiKey.id == key_id).values(is_active=False))
        elif change == "inactive_user":
            await db.execute(update(User).where(User.id == env.world.owner).values(is_active=False))
        else:
            await db.execute(
                update(ApiKey).where(ApiKey.id == key_id).values(scopes=["projects:read"])
            )
        await db.commit()
    release.set()
    with pytest.raises(AuthServiceError):
        await operation
    async with env.world.sessions() as db:
        assert (
            await db.execute(
                select(func.count())
                .select_from(ProjectFile)
                .where(ProjectFile.project_id == env.world.project)
            )
        ).scalar_one() == 0
        assert (
            await db.execute(
                select(func.count())
                .select_from(DocumentSubmission)
                .where(
                    DocumentSubmission.actor_user_id == env.world.owner,
                    DocumentSubmission.request_id == request_id,
                )
            )
        ).scalar_one() == 0
    assert not list(env.world.storage.root.rglob("*.txt"))
