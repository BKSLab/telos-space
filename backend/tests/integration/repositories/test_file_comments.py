"""Составные отправки, повторы, права и каскады на изолированном PostgreSQL."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import delete, event, func, select, update

from src.core.settings import get_settings
from src.db.models.file_comment_submissions import FileCommentSubmission
from src.db.models.file_processing_jobs import FileProcessingJob
from src.db.models.knowledge_attachment_texts import KnowledgeAttachmentText
from src.db.models.knowledge_index_jobs import KnowledgeIndexJob
from src.db.models.knowledge_project_file_texts import KnowledgeProjectFileText
from src.db.models.project_members import ProjectMember
from src.db.models.project_stages import ProjectStage
from src.db.models.task_activity import TaskActivity
from src.db.models.task_attachments import TaskAttachment
from src.db.models.task_comment_attachments import TaskCommentAttachment
from src.db.models.task_comments import TaskComment
from src.db.models.tasks import Task
from src.db.models.users import User
from src.dependencies.file_comments import build_file_comments_scope
from src.dependencies.scopes import build_project_files_scope
from src.exceptions.access import ResourceNotAvailableError
from src.exceptions.auth import InactiveUserError
from src.exceptions.file_comments import (
    FileCommentsRepositoryError,
    FileCommentsServiceError,
    FileCommentSubmissionConflictError,
    FileCommentValidationError,
)
from src.exceptions.task_attachments import TaskAttachmentNotFoundError
from src.exceptions.unit_of_work import UnitOfWorkRepositoryError
from src.schemas.task_transfers import TaskTransferTargetSchema
from src.services.file_comments import CommentFileUpload, FileCommentsService
from src.services.file_metadata import FilePreviewsService, TaskFileMutationsService
from src.services.project_files import ProjectFilesService
from src.services.task_comments import TaskCommentsService
from src.storage.task_attachments import TaskAttachmentStorage
from src.utils.document_refs import decode_document_ref, encode_document_ref
from tests.integration.repositories.test_agent_conversations_repository import (
    agent_env as _agent_env,
)
from tests.integration.repositories.test_personal_tasks_and_moves import services

isolated_file_db = _agent_env


@pytest_asyncio.fixture
async def file_env(isolated_file_db, tmp_path, monkeypatch):
    """Использует только отдельную synthetic-БД и отдельный uploads в tmp_path."""
    monkeypatch.setattr(
        "src.services.auth.decode_access_token", lambda token: {"user-1": 1, "user-2": 2}.get(token)
    )
    settings = get_settings().model_copy(
        update={
            "knowledge": get_settings().knowledge.model_copy(update={"knowledge_enabled": False})
        }
    )
    storage = TaskAttachmentStorage(tmp_path)
    scope = build_file_comments_scope(
        session_factory=isolated_file_db.factory, settings=settings, storage=storage
    )
    async with isolated_file_db.factory() as db:
        stages = [
            ProjectStage(project_id=i, name="Работа", order_index=0, color="#334455")
            for i in (1, 2)
        ]
        db.add_all(stages)
        await db.flush()
        tasks = [
            Task(
                project_id=pid,
                stage_id=stages[pid - 1].id,
                number=number,
                title=f"Задача {pid}:{number}",
                priority="MEDIUM",
                position=number,
            )
            for pid, number in ((1, 1), (1, 2), (2, 1))
        ]
        db.add_all(tasks)
        await db.flush()
        task_ids = [item.id for item in tasks]
        await db.commit()
    yield SimpleNamespace(
        factory=isolated_file_db.factory,
        engine=isolated_file_db.engine,
        scope=scope,
        storage=storage,
        path=tmp_path,
        task_id=task_ids[0],
        other_task_id=task_ids[1],
        foreign_task_id=task_ids[2],
        service=FileCommentsService(scope=scope, storage=storage, knowledge_enabled=False),
        previews=FilePreviewsService(scope=scope, storage=storage),
        mutations=TaskFileMutationsService(scope=scope, storage=storage),
        project_files=ProjectFilesService(
            scope=build_project_files_scope(
                session_factory=isolated_file_db.factory, settings=settings
            ),
            storage=storage,
            max_file_size=10 * 1024 * 1024,
            knowledge_enabled=False,
        ),
    )


async def submit(
    env,
    *,
    request_id=None,
    uploads=None,
    attachment_ids=None,
    document_refs=None,
    body_md="Комментарий",
    user=1,
    task_id=None,
):
    return await env.service.submit(
        task_id=task_id or env.task_id,
        request_id=request_id or uuid4(),
        body_md=body_md,
        uploads=uploads or [],
        document_refs=[
            *(document_refs or []),
            *(encode_document_ref("attachment", i) for i in attachment_ids or []),
        ],
        session_token=f"user-{user}",
        bearer_secret=None,
    )


def image_upload() -> CommentFileUpload:
    """Синтетический растр больше миниатюры без внешнего vision."""
    output = BytesIO()
    Image.new("RGB", (1600, 900), "#334455").save(output, "PNG")
    return CommentFileUpload("screen.png", output.getvalue(), "image/png", "  Проверка макета  ")


async def counts(env) -> tuple[int, ...]:
    async with env.factory() as db:
        return tuple(
            [
                await db.scalar(select(func.count()).select_from(model))
                for model in (
                    TaskComment,
                    TaskAttachment,
                    TaskCommentAttachment,
                    TaskActivity,
                    FileCommentSubmission,
                    FileProcessingJob,
                    KnowledgeIndexJob,
                )
            ]
        )


async def test_compound_rollback_compensates_originals_and_all_database_effects(file_env):
    env = file_env
    baseline = await counts(env)

    class FailingCommit:
        async def commit(self):
            raise UnitOfWorkRepositoryError("synthetic commit failure")

    @asynccontextmanager
    async def failing_scope():
        async with env.scope() as db:
            yield replace(db, unit_of_work=FailingCommit())

    env.service = FileCommentsService(
        scope=failing_scope, storage=env.storage, knowledge_enabled=False
    )
    with pytest.raises(FileCommentsServiceError):
        await submit(env, uploads=[image_upload()])
    assert await counts(env) == baseline
    assert not [path for path in env.path.rglob("*") if path.is_file()]


async def test_concurrent_same_uuid_has_one_comment_original_history_and_receipt(file_env):
    env = file_env
    request_id = uuid4()
    original_save = env.storage.save
    entered, release = asyncio.Event(), asyncio.Event()
    prepared_count = 0

    async def save(*args, **kwargs):
        nonlocal prepared_count
        # Обе длительные фазы находятся за пределами короткой DB-области.
        assert env.engine.pool.checkedout() <= 1
        key = await original_save(*args, **kwargs)
        prepared_count += 1
        if prepared_count == 2:
            entered.set()
        await release.wait()
        return key

    env.storage.save = save
    operations = [
        asyncio.create_task(
            submit(env, request_id=request_id, uploads=[image_upload()], body_md="")
        )
        for _ in range(2)
    ]
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert env.engine.pool.checkedout() == 0
    finally:
        release.set()
    first, second = await asyncio.wait_for(asyncio.gather(*operations), 10)
    env.storage.save = original_save
    assert first.model_dump() == second.model_dump()
    assert first.body_md == "" and len(first.document_refs) == 1
    async with env.scope() as db:
        document = await db.document_catalog.get(document_ref=first.document_refs[0], user_id=1)
        assert document.caption == "Проверка макета"
        assert document.author.id == 1
        assert document.states.semantic.status == "disabled"
    assert (await counts(env))[:5] == (1, 1, 1, 1, 1)
    assert len(list(env.path.rglob("*.png"))) == len(list(env.path.rglob("*.preview.jpg"))) == 1
    replay = await submit(env, request_id=request_id, uploads=[image_upload()], body_md="")
    assert replay.model_dump() == first.model_dump()
    assert len(list(env.path.rglob("*.png"))) == 1
    with pytest.raises(FileCommentSubmissionConflictError):
        await submit(env, request_id=request_id, uploads=[image_upload()], body_md="Иной текст")
    with pytest.raises(FileCommentSubmissionConflictError):
        await submit(
            env,
            request_id=request_id,
            uploads=[CommentFileUpload("other.txt", b"other")],
            body_md="",
        )


async def test_same_task_constraint_cross_task_rejected_and_replay_checks_current_access(file_env):
    env = file_env
    foreign = await submit(
        env, task_id=env.other_task_id, uploads=[CommentFileUpload("same.txt", b"file")]
    )
    file_id = decode_document_ref(foreign.document_refs[0])[1]
    with pytest.raises(FileCommentValidationError):
        await submit(env, attachment_ids=[file_id])
    text = await submit(env)
    async with env.scope() as db:
        with pytest.raises(FileCommentsRepositoryError):
            await db.links.save_many(
                task_id=env.task_id, comment_id=text.id, attachment_ids=[file_id]
            )
    async with env.factory() as db:
        assert await db.scalar(select(func.count()).select_from(TaskCommentAttachment)) == 1
    request_id = uuid4()
    await submit(env, request_id=request_id, user=2)
    async with env.factory() as db:
        await db.execute(
            delete(ProjectMember).where(ProjectMember.project_id == 1, ProjectMember.user_id == 2)
        )
        await db.commit()
    with pytest.raises(ResourceNotAvailableError):
        await submit(env, request_id=request_id, user=2)
    async with env.factory() as db:
        await db.execute(update(User).where(User.id == 1).values(is_active=False))
        await db.commit()
    with pytest.raises(InactiveUserError):
        await submit(env, request_id=uuid4())


async def test_delete_comment_retains_file_delete_file_unlinks_and_move_keeps_relation(file_env):
    env = file_env
    first = await submit(env, uploads=[CommentFileUpload("saved.txt", b"retained")])
    file_id = decode_document_ref(first.document_refs[0])[1]
    second = await submit(env, attachment_ids=[file_id])
    async with env.scope() as db:
        comments = TaskCommentsService(
            db.comments,
            db.tasks,
            db.activity,
            db.unit_of_work,
            db.knowledge_events,
            knowledge_enabled=False,
        )
        await comments.delete_comment(first.id)
        assert await db.files.get_by_id_for_task(file_id, env.task_id) is not None
    assert len(list(env.path.rglob("*.txt"))) == 1
    async with env.factory() as db:
        _, moves = services(db, env.path)
        moved = await moves.move(
            task_id=env.task_id, target=TaskTransferTargetSchema(project_id=2), user_id=1
        )
        assert moved.task.id == env.task_id
    async with env.scope() as db:
        restored = await db.comments.get_by_id(second.id)
        assert [item.id for item in restored.attachments] == [file_id]
        await db.attachments.delete_attachment(task_id=env.task_id, attachment_id=file_id)
    async with env.scope() as db:
        restored = await db.comments.get_by_id(second.id)
        assert restored.attachments == []
    assert not list(env.path.rglob("*.txt"))


async def test_comments_batch_load_has_constant_queries_and_preview_rechecks_current_placement(
    file_env,
):
    env = file_env
    first = await submit(env, uploads=[image_upload()])
    file_id = decode_document_ref(first.document_refs[0])[1]
    for _ in range(3):
        await submit(env, attachment_ids=[file_id])
    queries = []

    def record(connection, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    async with env.scope() as db:
        comments = TaskCommentsService(
            db.comments,
            db.tasks,
            db.activity,
            db.unit_of_work,
            db.knowledge_events,
            knowledge_enabled=False,
        )
        event.listen(env.engine.sync_engine, "before_cursor_execute", record)
        try:
            result = await comments.get_comments(env.task_id)
        finally:
            event.remove(env.engine.sync_engine, "before_cursor_execute", record)
    assert len(result) == 4 and all(
        item.document_refs == [encode_document_ref("attachment", file_id)] for item in result
    )
    assert len(queries) == 4
    preview = await env.previews.prepare_task(
        task_id=env.task_id, attachment_id=file_id, session_token="user-2", bearer_secret=None
    )
    assert env.engine.pool.checkedout() == 0
    with Image.open(preview.path) as image:
        assert image.format == "JPEG" and max(image.size) <= env.storage.PREVIEW_SIZE[0]
    original = next(env.path.rglob("*.png"))
    with Image.open(original) as image:
        assert image.size == (1600, 900)
    async with env.factory() as db:
        _, moves = services(db, env.path)
        await moves.move(
            task_id=env.task_id, target=TaskTransferTargetSchema(project_id=2), user_id=1
        )
    with pytest.raises(ResourceNotAvailableError):
        await env.previews.prepare_task(
            task_id=env.task_id, attachment_id=file_id, session_token="user-2", bearer_secret=None
        )
    with pytest.raises(ResourceNotAvailableError):
        await env.previews.prepare_task(
            task_id=env.foreign_task_id,
            attachment_id=file_id,
            session_token="user-2",
            bearer_secret=None,
        )


async def test_caption_preserves_text_original_and_processing_generation_and_retry_preserves_ready_cache(
    file_env,
):
    env = file_env
    original_save = env.storage.save

    async def save(*args, **kwargs):
        assert env.engine.pool.checkedout() == 0
        return await original_save(*args, **kwargs)

    env.storage.save = save
    uploaded = await env.mutations.upload(
        task_id=env.task_id,
        file_name="facts.txt",
        content_type="text/plain",
        content=b"original facts",
        caption=" Исходная подпись ",
        session_token="user-2",
        bearer_secret=None,
    )
    env.storage.save = original_save
    assert uploaded.caption == "Исходная подпись" and uploaded.uploaded_by_user_id == 2
    assert uploaded.index_status == "disabled"
    project_file = await env.project_files.upload(
        project_id=1,
        file_name="project.txt",
        content=b"project original",
        caption="Проект",
        session_token="user-1",
        bearer_secret=None,
    )
    assert project_file.uploaded_by_user_id == 1 and project_file.caption == "Проект"
    async with env.factory() as db:
        db.add(
            KnowledgeAttachmentText(
                attachment_id=uploaded.id,
                text="Уже извлечённый текст",
                status="ready",
                content_hash="a" * 64,
                original_chars=21,
            )
        )
        db.add(
            KnowledgeProjectFileText(
                project_file_id=project_file.id,
                text="Сохранённый текст проекта",
                status="ready",
                content_hash="b" * 64,
                original_chars=25,
            )
        )
        await db.execute(update(FileProcessingJob).values(status="ready"))
        await db.commit()
        initial_versions = dict(
            (
                await db.execute(select(FileProcessingJob.id, FileProcessingJob.requested_version))
            ).all()
        )
        assert (
            await db.scalar(select(TaskAttachment.task_id).where(TaskAttachment.id == uploaded.id))
            == env.task_id
        )
    caption = await env.mutations.update_caption(
        task_id=env.task_id,
        attachment_id=uploaded.id,
        caption=" Новая подпись ",
        session_token="user-2",
        bearer_secret=None,
    )
    assert (
        caption.caption == "Новая подпись"
        and caption.extraction_status == caption.fts_status == "ready"
    )
    updated = await env.project_files.update_caption(
        project_id=1, file_id=project_file.id, user_id=1, caption="  Новый проект  "
    )
    assert updated.caption == "Новый проект" and updated.extraction_status == "ready"
    async with env.factory() as db:
        assert (
            dict(
                (
                    await db.execute(
                        select(FileProcessingJob.id, FileProcessingJob.requested_version)
                    )
                ).all()
            )
            == initial_versions
        )
        cache = await db.get(KnowledgeAttachmentText, uploaded.id)
        assert cache.text == "Уже извлечённый текст" and cache.content_hash == "a" * 64
        cache = await db.get(KnowledgeProjectFileText, project_file.id)
        assert cache.text == "Сохранённый текст проекта" and cache.content_hash == "b" * 64
    with pytest.raises(TaskAttachmentNotFoundError):
        await env.mutations.update_caption(
            task_id=env.other_task_id,
            attachment_id=uploaded.id,
            caption="Чужая задача",
            session_token="user-1",
            bearer_secret=None,
        )
    retry = await env.mutations.retry(
        task_id=env.task_id, attachment_id=uploaded.id, session_token="user-2", bearer_secret=None
    )
    assert (
        retry.extraction_status == "ready"
        and retry.fts_status == "pending"
        and retry.index_status == "disabled"
    )
    async with env.factory() as db:
        cache = await db.get(KnowledgeAttachmentText, uploaded.id)
        job = (
            await db.execute(
                select(FileProcessingJob).where(FileProcessingJob.attachment_id == uploaded.id)
            )
        ).scalar_one()
        assert cache.status == "ready" and cache.text == "Уже извлечённый текст"
        assert job.status == "pending" and job.requested_version == initial_versions[job.id] + 1
        # Retry during active processing requests another generation without stealing its lease.
        await db.execute(
            update(FileProcessingJob)
            .where(FileProcessingJob.id == job.id)
            .values(
                status="processing",
                lease_token="synthetic-active-lease",
                claimed_version=job.requested_version,
            )
        )
        await db.commit()
    await env.mutations.retry(
        task_id=env.task_id, attachment_id=uploaded.id, session_token="user-1", bearer_secret=None
    )
    async with env.factory() as db:
        job = (
            await db.execute(
                select(FileProcessingJob).where(FileProcessingJob.attachment_id == uploaded.id)
            )
        ).scalar_one()
        assert job.status == "processing" and job.lease_token == "synthetic-active-lease"
        assert job.requested_version == job.claimed_version + 1
    assert sorted(path.read_bytes() for path in env.path.rglob("*.txt")) == [
        b"original facts",
        b"project original",
    ]
    with pytest.raises(ResourceNotAvailableError):
        await env.previews.prepare_project(
            project_id=2, file_id=project_file.id, session_token="user-2", bearer_secret=None
        )


async def test_comment_uses_all_document_types_and_transfer_retains_only_owned_documents(file_env):
    """Ссылки сохраняют один оригинал; detach/transfer не раскрывают старый контекст."""
    from src.schemas.document_catalog import DocumentCreateRequestSchema

    env = file_env
    async with env.scope() as db:
        shared = await db.document_catalog.create(
            user_id=1,
            data=DocumentCreateRequestSchema(
                request_id=uuid4(),
                project_id=1,
                title="Общий Markdown",
                content_md="Условия",
            ),
        )
        owned = await db.document_catalog.create(
            user_id=1,
            data=DocumentCreateRequestSchema(
                request_id=uuid4(),
                owner_task_id=env.task_id,
                title="Заметки задачи",
                content_md="Результат",
            ),
        )
        uploaded = await db.document_catalog.upload(
            user_id=1,
            request_id=uuid4(),
            project_id=1,
            file_name="brief.md",
            content_type="text/markdown",
            content=b"# Original",
        )
        for item in (shared, uploaded):
            await db.document_catalog.link(document_ref=item.ref, task_id=env.task_id, user_id=1)
        await db.unit_of_work.commit()
    refs = [shared.ref, owned.ref, uploaded.ref]
    request_id = uuid4()
    comment = await submit(env, request_id=request_id, document_refs=refs)
    assert set(comment.document_refs) == set(refs)
    replay = await submit(env, request_id=request_id, document_refs=refs)
    assert replay.model_dump() == comment.model_dump()
    async with env.scope() as db:
        await db.document_catalog.unlink(document_ref=shared.ref, task_id=env.task_id, user_id=1)
        await db.unit_of_work.commit()
        assert set((await db.comments.get_document_refs([comment.id]))[comment.id]) == {
            owned.ref,
            uploaded.ref,
        }
        assert (await db.document_catalog.get(document_ref=shared.ref, user_id=1)).ref == shared.ref
    async with env.factory() as db:
        _, moves = services(db, env.path)
        await moves.move(
            task_id=env.task_id, target=TaskTransferTargetSchema(project_id=2), user_id=1
        )
    async with env.scope() as db:
        assert (await db.comments.get_document_refs([comment.id]))[comment.id] == [owned.ref]
        moved = await db.document_catalog.get(document_ref=owned.ref, user_id=1)
        assert moved.ownership.project_id == 2
        original = await db.document_catalog.get(document_ref=uploaded.ref, user_id=1)
        assert original.ownership.project_id == 1
    assert len(list(env.path.rglob("*.md"))) == 1


async def test_deleted_project_upload_disappears_from_comment_before_worker_cleanup(file_env):
    env = file_env
    async with env.scope() as db:
        document = await db.document_catalog.upload(
            user_id=1,
            request_id=uuid4(),
            project_id=1,
            file_name="deleted.txt",
            content_type="text/plain",
            content=b"original",
        )
        await db.document_catalog.link(document_ref=document.ref, task_id=env.task_id, user_id=1)
        await db.unit_of_work.commit()
    comment = await submit(env, document_refs=[document.ref])
    async with env.scope() as db:
        await db.document_catalog.delete(document_ref=document.ref, user_id=1)
        await db.unit_of_work.commit()
        assert (await db.comments.get_document_refs([comment.id])).get(comment.id, []) == []
    assert len(list(env.path.rglob("*.txt"))) == 1
