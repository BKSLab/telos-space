"""Настоящий PostgreSQL: независимое извлечение, GIN, lease и актуальный доступ."""

import asyncio
import hashlib
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.clients.vision import DisabledVisionCapability
from src.db.models.api_tokens import ApiTokenScope
from src.db.models.document_links import DocumentLink
from src.db.models.documents import Document
from src.db.models.file_processing_jobs import FileProcessingJob
from src.db.models.file_search_chunks import FileSearchChunk
from src.db.models.knowledge_attachment_texts import KnowledgeAttachmentText
from src.db.models.knowledge_index_jobs import KnowledgeIndexJob
from src.db.models.knowledge_project_file_texts import KnowledgeProjectFileText
from src.db.models.project_files import ProjectFile
from src.db.models.project_members import ProjectMember
from src.db.models.project_stages import ProjectStage
from src.db.models.projects import Project
from src.db.models.task_attachments import TaskAttachment
from src.db.models.task_participants import TaskParticipant
from src.db.models.tasks import Task
from src.db.models.users import User
from src.dependencies.file_comments import build_file_comments_scope
from src.dependencies.file_processing import (
    build_file_processing_worker,
    build_file_search_backfill,
)
from src.dependencies.scopes import build_project_files_scope
from src.exceptions.clients import VisionClientError
from src.repositories.file_processing_jobs import FileProcessingJobsRepository
from src.repositories.file_search import FileSearchRepository
from src.services.auth import AuthService, Principal
from src.services.file_metadata import TaskFileMutationsService
from src.services.file_processing import FileProcessingConfig
from src.services.project_files import ProjectFilesService
from src.storage.task_attachments import TaskAttachmentStorage


@dataclass
class World:
    sessions: async_sessionmaker
    storage: TaskAttachmentStorage
    owner: int
    stranger: int
    project: int
    other_project: int
    stage: int
    other_stage: int
    task: int
    personal_task: int


@pytest_asyncio.fixture
async def world(engine, tmp_path: Path):
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex[:8]
    async with sessions() as db:
        users = [
            User(
                username=f"files-{suffix}-{i}",
                password_hash="!",
                first_name="Тест",
                last_name="Файлов",
                is_active=True,
            )
            for i in range(2)
        ]
        db.add_all(users)
        await db.flush()
        projects = [
            Project(
                owner_id=users[i].id,
                key=f"F{suffix[:7]}{i}",
                name="Файлы",
                color="#123456",
                order_index=0,
            )
            for i in range(2)
        ]
        db.add_all(projects)
        await db.flush()
        db.add_all(
            [
                ProjectMember(project_id=project.id, user_id=user.id, role="OWNER")
                for project, user in zip(projects, users, strict=True)
            ]
        )
        stages = [
            ProjectStage(project_id=project.id, name="Открыто", color="#123456")
            for project in projects
        ]
        db.add_all(stages)
        await db.flush()
        tasks = [
            Task(project_id=projects[0].id, stage_id=stages[0].id, number=1, title="Проектная"),
            Task(
                project_id=None,
                stage_id=None,
                status="NEW",
                number=int(suffix[:7], 16),
                title="Личная",
            ),
        ]
        db.add_all(tasks)
        await db.flush()
        db.add(TaskParticipant(task_id=tasks[1].id, user_id=users[0].id, role="REPORTER"))
        await db.commit()
        fixture = World(
            sessions,
            TaskAttachmentStorage(tmp_path),
            users[0].id,
            users[1].id,
            projects[0].id,
            projects[1].id,
            stages[0].id,
            stages[1].id,
            tasks[0].id,
            tasks[1].id,
        )
    yield fixture
    async with sessions() as db:
        await db.execute(delete(Task).where(Task.id.in_([fixture.task, fixture.personal_task])))
        await db.execute(
            delete(Project).where(Project.id.in_([fixture.project, fixture.other_project]))
        )
        await db.execute(delete(User).where(User.id.in_([fixture.owner, fixture.stranger])))
        # Outbox удаления collection намеренно переживает сам проект без FK.
        # Удаляем только задания synthetic-проектов после всех teardown-триггеров.
        await db.execute(
            delete(KnowledgeIndexJob).where(
                KnowledgeIndexJob.project_id.in_([fixture.project, fixture.other_project])
            )
        )
        await db.commit()


async def add_file(
    world: World,
    *,
    attachment: bool = False,
    personal: bool = False,
    name: str = "поставка.txt",
    content: bytes = "Извлечённые требования поставки".encode(),
    caption: str = "Янтарный паспорт",
) -> int:
    task_id = world.personal_task if personal else world.task
    storage_key = await world.storage.save(task_id, Path(name).suffix, content)
    async with world.sessions() as db:
        values = dict(
            original_name=name,
            caption=caption,
            storage_key=storage_key,
            content_type="image/png" if name.endswith(".png") else "text/plain",
            size=len(content),
        )
        source = (
            TaskAttachment(task_id=task_id, **values)
            if attachment
            else ProjectFile(
                project_id=world.project, content_hash=hashlib.sha256(content).hexdigest(), **values
            )
        )
        db.add(source)
        await db.commit()
        return source.id


def worker(world: World, vision=None, *, max_attempts: int = 3):
    return build_file_processing_worker(
        session_factory=world.sessions,
        storage=world.storage,
        vision=vision or DisabledVisionCapability(),
        config=FileProcessingConfig(
            poll_seconds=0.01, max_attempts=max_attempts, lease_seconds=60, retry_seconds=0
        ),
    )


async def search(
    world: World,
    query: str,
    *,
    user_id=None,
    project_id=None,
    task_id=None,
    scope="all",
    limit=30,
    offset=0,
):
    async with world.sessions() as db:
        return await FileSearchRepository(db).search(
            query=query,
            user_id=user_id or world.owner,
            project_id=project_id
            if project_id is not None
            else (None if task_id == world.personal_task else world.project),
            task_id=task_id,
            scope=scope,
            limit=limit,
            offset=offset,
        )


class BlockingVision:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def extract_image_text(self, *, filename: str, content: bytes) -> str:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return "Распознанная кварцосхема"


async def test_base_worker_processes_text_without_any_semantic_dependency(world):
    file_id = await add_file(world)
    task_file_id = await add_file(world, attachment=True)
    assert (await search(world, "Янтарный"))["total"] == 2
    assert not (await search(world, "Извлечённые"))["items"]
    base = worker(world)
    assert await base.process_next()
    assert await base.process_next()
    page = await search(world, "Извлечённые")
    assert {(item["source_type"], item["source_id"]) for item in page["items"]} == {
        ("project_file", file_id),
        ("task_attachment", task_file_id),
    }
    async with world.sessions() as db:
        assert (
            await db.scalar(
                select(KnowledgeProjectFileText.status).where(
                    KnowledgeProjectFileText.project_file_id == file_id
                )
            )
            == "ready"
        )
        assert (
            await db.scalar(
                select(KnowledgeAttachmentText.status).where(
                    KnowledgeAttachmentText.attachment_id == task_file_id
                )
            )
            == "ready"
        )


async def test_concurrent_workers_caption_and_retry_call_vision_once(world):
    file_id = await add_file(world, name="схема.png", content=b"synthetic image")
    vision = BlockingVision()
    first, second = worker(world, vision), worker(world, vision)
    processing = asyncio.create_task(first.process_next())
    await asyncio.wait_for(vision.started.wait(), 5)
    assert not await second.process_next()
    async with world.sessions() as db:
        await db.execute(
            update(ProjectFile)
            .where(ProjectFile.id == file_id)
            .values(caption="Обновлённый акцент", retry_requested_at=func.now())
        )
        await db.commit()
    assert (await search(world, "Обновлённый"))["total"] == 1
    vision.release.set()
    assert await processing
    assert await second.process_next()
    assert vision.calls == 1
    async with world.sessions() as db:
        state = (
            await db.execute(
                select(FileProcessingJob).where(FileProcessingJob.project_file_id == file_id)
            )
        ).scalar_one()
        assert state.status == "ready" and state.requested_version == 2
    assert (await search(world, "кварцосхема"))["total"] == 1


async def test_original_change_during_extraction_rejects_stale_result(world):
    file_id = await add_file(world, content=b"old original")
    base = worker(world)
    claimed = await base.service.claim_next()
    old_result = await base.service.extract(claimed)
    replacement = "Новая гранитоспецификация".encode()
    key = await world.storage.save(world.task, ".txt", replacement)
    async with world.sessions() as db:
        await db.execute(
            update(ProjectFile)
            .where(ProjectFile.id == file_id)
            .values(
                storage_key=key,
                size=len(replacement),
                content_hash=hashlib.sha256(replacement).hexdigest(),
            )
        )
        await db.commit()
    assert not await base.service.persist(claimed, old_result)
    assert await base.process_next()
    assert not (await search(world, "original"))["items"]
    assert (await search(world, "гранитоспецификация"))["total"] == 1


async def test_soft_delete_during_processing_cleans_original_without_semantics(world):
    file_id = await add_file(world)
    base = worker(world)
    claimed = await base.service.claim_next()
    extracted = await base.service.extract(claimed)
    path = world.storage.resolve(claimed.snapshot["storage_key"])
    async with world.sessions() as db:
        await db.execute(
            update(ProjectFile).where(ProjectFile.id == file_id).values(deleted_at=func.now())
        )
        await db.commit()
    assert not (await search(world, "Янтарный"))["items"]
    assert not await base.service.persist(claimed, extracted)
    assert await base.process_next()
    assert not path.exists()
    async with world.sessions() as db:
        assert await db.get(ProjectFile, file_id) is None
        assert (
            await db.scalar(
                select(func.count())
                .select_from(FileProcessingJob)
                .where(FileProcessingJob.project_file_id == file_id)
            )
            == 0
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(KnowledgeProjectFileText)
                .where(KnowledgeProjectFileText.project_file_id == file_id)
            )
            == 0
        )


async def test_long_text_tail_offsets_weighted_vector_and_gin(world):
    content = ("Обычные условия договора. " * 24000 + "Гиперхвостмаркер уникальный").encode()
    file_id = await add_file(world, content=content)
    assert await worker(world).process_next()
    page = await search(world, "Гиперхвостмаркер")
    assert page["total"] == 1 and page["items"][0]["source_id"] == file_id
    assert "__FTS_START__" in page["items"][0]["excerpt"]
    async with world.sessions() as db:
        cached = await db.get(KnowledgeProjectFileText, file_id)
        rows = (
            (
                await db.execute(
                    select(FileSearchChunk)
                    .where(FileSearchChunk.project_file_id == file_id)
                    .order_by(FileSearchChunk.chunk_index)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) > 10 and rows[-1].end_offset == len(cached.text)
        assert all(
            row.body == cached.text[row.start_offset : row.end_offset] and len(row.body) <= 12000
            for row in rows
        )
        await db.execute(text("SET LOCAL enable_seqscan = off"))
        plan = (
            await db.execute(
                text(
                    "EXPLAIN (FORMAT JSON) SELECT id FROM file_search_chunks WHERE search_vector @@ websearch_to_tsquery('russian', 'Гиперхвостмаркер')"
                )
            )
        ).scalar_one()
        assert "ix_file_search_vector" in str(plan)


async def test_current_task_project_personal_acl_and_linked_documents(world):
    attachment_id = await add_file(world, attachment=True)
    await add_file(world, attachment=True, personal=True)
    async with world.sessions() as db:
        doc = Document(
            project_id=world.project,
            slug="contract",
            title="Янтарный документ",
            content_md="Связанный текст",
        )
        foreign = Document(
            project_id=world.other_project,
            slug="foreign",
            title="Янтарный секрет",
            content_md="Чужой текст",
        )
        db.add_all([doc, foreign])
        await db.flush()
        db.add_all(
            [
                DocumentLink(document_id=doc.id, task_id=world.task),
                DocumentLink(document_id=foreign.id, task_id=world.task),
            ]
        )
        await db.commit()
    assert (await search(world, "Янтарный", task_id=world.task))["total"] == 2
    assert (await search(world, "Янтарный", task_id=world.personal_task))["total"] == 1
    assert (await search(world, "Янтарный", user_id=world.stranger, task_id=world.personal_task))[
        "total"
    ] == 0
    async with world.sessions() as db:
        await db.execute(
            update(Task)
            .where(Task.id == world.task)
            .values(project_id=world.other_project, stage_id=world.other_stage)
        )
        await db.commit()
    assert (await search(world, "Янтарный", scope="task_files"))["total"] == 0
    moved = await search(
        world,
        "Янтарный",
        user_id=world.stranger,
        project_id=world.other_project,
        scope="task_files",
    )
    assert [item["source_id"] for item in moved["items"]] == [attachment_id]
    assert (await search(world, "Янтарный", task_id=world.task))["total"] == 0
    async with world.sessions() as db:
        await db.execute(delete(Task).where(Task.id == world.task))
        await db.commit()
        assert (
            await db.scalar(
                select(func.count())
                .select_from(FileSearchChunk)
                .where(FileSearchChunk.attachment_id == attachment_id)
            )
            == 0
        )


async def test_lease_recovery_rejects_old_owner_and_raw_insert_rolls_back_atomically(world):
    file_id = await add_file(world)
    base = worker(world)
    claimed = await base.service.claim_next()
    async with world.sessions() as db:
        await db.execute(
            update(FileProcessingJob)
            .where(FileProcessingJob.id == claimed.job_id)
            .values(lease_until=func.now() - text("interval '1 second'"))
        )
        await db.commit()
    successor = await base.service.claim_next()
    assert successor.job_id == claimed.job_id and successor.lease_token != claimed.lease_token
    assert not await base.service.persist(claimed, await base.service.extract(claimed))
    assert await base.service.persist(successor, await base.service.extract(successor))
    async with world.sessions() as db:
        source = await db.get(ProjectFile, file_id)
        assert source is not None
        await db.execute(
            text(
                "INSERT INTO project_files (project_id,original_name,content_type,size,storage_key,content_hash) VALUES (:project,'Откат.txt','text/plain',4,'rollback-file',:hash)"
            ),
            {"project": world.project, "hash": "a" * 64},
        )
        assert await db.scalar(select(func.count()).select_from(FileProcessingJob)) >= 2
        await db.rollback()
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ProjectFile)
                .where(ProjectFile.storage_key == "rollback-file")
            )
            == 0
        )


async def test_cache_only_backfill_is_idempotent_and_does_not_enqueue(world):
    file_id = await add_file(world)
    async with world.sessions() as db:
        db.add(
            KnowledgeProjectFileText(
                project_file_id=file_id,
                content_hash=hashlib.sha256("Извлечённые требования поставки".encode()).hexdigest(),
                text="Сохранённая аквамаринспецификация",
                original_chars=34,
                status="ready",
                index_status="pending",
            )
        )
        await db.commit()
        await db.execute(delete(FileProcessingJob))
        await db.execute(delete(FileSearchChunk))
        await db.commit()
    backfill = build_file_search_backfill(session_factory=world.sessions)
    assert await backfill.run(batch_size=1) == {"task_attachment": 0, "project_file": 1}
    assert await backfill.run(batch_size=1) == {"task_attachment": 0, "project_file": 1}
    assert (await search(world, "аквамаринспецификация"))["total"] == 1
    async with world.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(FileProcessingJob)) == 0
        assert await db.scalar(select(func.count()).select_from(FileSearchChunk)) == 1


async def test_backfill_enqueue_missing_is_explicit_and_preserves_existing_leases(world):
    ready_id = await add_file(world)
    missing_id = await add_file(world, caption="Прежний необработанный")
    base = worker(world)
    assert await base.process_next()
    claimed = await base.service.claim_next()
    async with world.sessions() as db:
        await db.execute(
            delete(FileProcessingJob).where(FileProcessingJob.project_file_id == ready_id)
        )
        await db.commit()
    backfill = build_file_search_backfill(session_factory=world.sessions)
    result = await backfill.run(batch_size=1, enqueue_missing=True)
    assert result["enqueued_project_file"] == 0
    async with world.sessions() as db:
        processing = (
            await db.execute(
                select(FileProcessingJob).where(FileProcessingJob.project_file_id == missing_id)
            )
        ).scalar_one()
        assert processing.status == "processing" and processing.lease_token == claimed.lease_token
        assert (
            await db.scalar(
                select(func.count())
                .select_from(FileProcessingJob)
                .where(FileProcessingJob.project_file_id == ready_id)
            )
            == 0
        )
        await db.execute(
            delete(FileProcessingJob).where(FileProcessingJob.project_file_id == missing_id)
        )
        await db.commit()
    result = await backfill.run(batch_size=1, enqueue_missing=True)
    assert result["enqueued_project_file"] == 1
    assert (await backfill.run(batch_size=1, enqueue_missing=True))["enqueued_project_file"] == 0


async def test_backfill_waits_for_original_then_reads_new_cache_snapshot(world):
    file_id = await add_file(world, content="Старый лазуритотекст".encode())
    assert await worker(world).process_next()
    backfill = build_file_search_backfill(session_factory=world.sessions)
    async with world.sessions() as writer:
        await writer.execute(
            select(ProjectFile.id).where(ProjectFile.id == file_id).with_for_update()
        )
        await writer.execute(
            update(KnowledgeProjectFileText)
            .where(KnowledgeProjectFileText.project_file_id == file_id)
            .values(text="Новый малахитотекст", original_chars=19)
        )
        rebuilding = asyncio.create_task(backfill.run(batch_size=1))
        try:
            async with world.sessions() as observer:
                blocked = False
                for _ in range(200):
                    blocked = await observer.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND query LIKE '%file_search_backfill_lock%' AND wait_event_type = 'Lock')"
                        )
                    )
                    await observer.rollback()
                    if blocked:
                        break
                    await asyncio.sleep(0.01)
                assert blocked, (
                    "Backfill должен ждать исходную строку, не захватывая старый cache snapshot"
                )
            await writer.commit()
            assert (await asyncio.wait_for(rebuilding, 5))["project_file"] == 1
        finally:
            if not rebuilding.done():
                rebuilding.cancel()
                await asyncio.gather(rebuilding, return_exceptions=True)
    assert (await search(world, "малахитотекст"))["total"] == 1
    assert (await search(world, "лазуритотекст"))["total"] == 0


async def test_project_retry_and_worker_persist_use_same_lock_order_and_keep_ready_fts(world):
    body = "Полный сохранённый текст оригинала. " * 800 + "Свежий топазохвост"
    file_id = await add_file(world, content=body.encode())
    base = worker(world)
    assert await base.process_next()
    async with world.sessions() as db:
        assert await FileProcessingJobsRepository(db).request_retry(project_file_id=file_id)
        await db.commit()
    claimed = await base.service.claim_next()
    assert claimed is not None
    extracted = await base.service.extract(claimed)
    assert extracted.status == "ready" and extracted.text == body

    original_locked = asyncio.Event()
    allow_persist = asyncio.Event()
    retry_started = asyncio.Event()
    retry_pid = None
    original_worker_scope = base.service.scope

    @asynccontextmanager
    async def paused_worker_scope():
        async with original_worker_scope() as db:
            original_get_snapshot = db.sources.get_snapshot

            async def get_snapshot(*, attachment_id, project_file_id, lock=False):
                snapshot = await original_get_snapshot(
                    attachment_id=attachment_id, project_file_id=project_file_id, lock=lock
                )
                if lock:
                    original_locked.set()
                    await allow_persist.wait()
                return snapshot

            db.sources.get_snapshot = get_snapshot
            yield db

    base.service.scope = paused_worker_scope
    project_scope = build_project_files_scope(
        session_factory=world.sessions,
        settings=SimpleNamespace(
            auth=SimpleNamespace(registration_invite_code=SecretStr("synthetic-invite"))
        ),
    )

    @asynccontextmanager
    async def observed_retry_scope():
        nonlocal retry_pid
        async with project_scope() as db:
            retry_pid = await db.files.db_session.scalar(select(func.pg_backend_pid()))
            retry_started.set()
            yield db

    files = ProjectFilesService(
        scope=observed_retry_scope,
        storage=world.storage,
        max_file_size=10 * 1024 * 1024,
        knowledge_enabled=False,
    )
    persistence = asyncio.create_task(base.service.persist(claimed, extracted))
    retrying = None
    try:
        await asyncio.wait_for(original_locked.wait(), 5)
        retrying = asyncio.create_task(
            files.retry(project_id=world.project, file_id=file_id, user_id=world.owner)
        )
        await asyncio.wait_for(retry_started.wait(), 5)
        async with world.sessions() as observer:
            blocked = False
            for _ in range(200):
                blocked = await observer.scalar(
                    text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"),
                    {"pid": retry_pid},
                )
                await observer.rollback()
                if blocked:
                    break
                await asyncio.sleep(0.01)
            assert blocked, "Повтор должен ждать оригинал, уже захваченный worker-ом"
        allow_persist.set()
        persisted, metadata = await asyncio.wait_for(asyncio.gather(persistence, retrying), 8)
        assert persisted and metadata.extraction_status == "ready"
    finally:
        allow_persist.set()
        unfinished = [
            task for task in (persistence, retrying) if task is not None and not task.done()
        ]
        for task in unfinished:
            task.cancel()
        await asyncio.gather(*unfinished, return_exceptions=True)

    async with world.sessions() as db:
        cached = await db.get(KnowledgeProjectFileText, file_id)
        assert (
            cached.status == "ready" and cached.text == body and cached.original_chars == len(body)
        )
        chunks = (
            (
                await db.execute(
                    select(FileSearchChunk)
                    .where(FileSearchChunk.project_file_id == file_id)
                    .order_by(FileSearchChunk.chunk_index)
                )
            )
            .scalars()
            .all()
        )
        assert len(chunks) > 1 and chunks[-1].end_offset == len(body)
        assert all(chunk.body == body[chunk.start_offset : chunk.end_offset] for chunk in chunks)
    assert (await search(world, "топазохвост"))["total"] == 1


async def test_task_caption_and_worker_persist_lock_task_before_attachment_without_lost_lease(
    world,
):
    body = "Полный текст задачи. " * 900 + "Сапфировый хвост"
    attachment_id = await add_file(world, attachment=True, content=body.encode())
    base = worker(world)
    assert await base.process_next()
    async with world.sessions() as db:
        assert await FileProcessingJobsRepository(db).request_retry(attachment_id=attachment_id)
        await db.commit()
    claimed = await base.service.claim_next()
    assert claimed is not None
    extracted = await base.service.extract(claimed)
    assert extracted.status == "ready" and extracted.text == body

    task_locked = asyncio.Event()
    allow_caption_update = asyncio.Event()
    worker_started = asyncio.Event()
    worker_pid = None
    settings = SimpleNamespace(
        auth=SimpleNamespace(registration_invite_code=SecretStr("synthetic-invite")),
        knowledge=SimpleNamespace(knowledge_enabled=False),
    )
    caption_scope = build_file_comments_scope(
        session_factory=world.sessions, settings=settings, storage=world.storage
    )
    principal = Principal(
        user_id=world.owner,
        username="synthetic-owner",
        last_name="Тестов",
        first_name="Тест",
        middle_name=None,
        scope=ApiTokenScope.WRITE,
        via_api_token=False,
    )
    auth = AsyncMock(spec=AuthService)
    auth.resolve_principal.return_value = principal

    @asynccontextmanager
    async def paused_caption_scope():
        async with caption_scope() as db:
            original_get_task = db.tasks.get_for_update

            async def get_for_update(*, task_id):
                task = await original_get_task(task_id=task_id)
                task_locked.set()
                await allow_caption_update.wait()
                return task

            db.tasks.get_for_update = get_for_update
            yield replace(db, auth=auth)

    original_worker_scope = base.service.scope

    @asynccontextmanager
    async def observed_worker_scope():
        nonlocal worker_pid
        async with original_worker_scope() as db:
            worker_pid = await db.sources.db_session.scalar(select(func.pg_backend_pid()))
            worker_started.set()
            yield db

    base.service.scope = observed_worker_scope
    files = TaskFileMutationsService(scope=paused_caption_scope, storage=world.storage)
    updating = asyncio.create_task(
        files.update_caption(
            task_id=world.task,
            attachment_id=attachment_id,
            caption="Новая опалоподпись",
            session_token=None,
            bearer_secret=None,
        )
    )
    persistence = None
    try:
        await asyncio.wait_for(task_locked.wait(), 5)
        persistence = asyncio.create_task(base.service.persist(claimed, extracted))
        await asyncio.wait_for(worker_started.wait(), 5)
        async with world.sessions() as observer:
            blocked = False
            for _ in range(200):
                blocked = await observer.scalar(
                    text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"),
                    {"pid": worker_pid},
                )
                await observer.rollback()
                if blocked:
                    break
                await asyncio.sleep(0.01)
            assert blocked, "Worker должен ждать Task, не захватывая вложение раньше родителя"
        allow_caption_update.set()
        metadata, persisted = await asyncio.wait_for(asyncio.gather(updating, persistence), 8)
        assert persisted and metadata.caption == "Новая опалоподпись"
    finally:
        allow_caption_update.set()
        unfinished = [
            task for task in (updating, persistence) if task is not None and not task.done()
        ]
        for task in unfinished:
            task.cancel()
        await asyncio.gather(*unfinished, return_exceptions=True)

    async with world.sessions() as db:
        cached = await db.get(KnowledgeAttachmentText, attachment_id)
        assert (
            cached.status == "ready" and cached.text == body and cached.original_chars == len(body)
        )
        job = (
            await db.execute(
                select(FileProcessingJob).where(FileProcessingJob.attachment_id == attachment_id)
            )
        ).scalar_one()
        assert job.status == "ready" and job.lease_token is None and job.requested_version == 2
    assert (await search(world, "опалоподпись"))["total"] == 1
    assert (await search(world, "Сапфировый"))["total"] == 1


async def test_stable_pagination_deduplicates_chunks_and_disabled_images_keep_metadata(world):
    for _ in range(3):
        await add_file(
            world, caption="Стабильная подпись", content=("Стабильная подпись. " * 2000).encode()
        )
    image_id = await add_file(
        world, name="кварцоснимок.png", caption="Доступная подпись", content=b"image bytes"
    )
    base = worker(world)
    while await base.process_next():
        pass
    pages = [await search(world, "Стабильная", limit=1, offset=index) for index in range(4)]
    assert all(page["total"] == 3 for page in pages)
    assert len({page["items"][0]["source_id"] for page in pages[:3]}) == 3
    assert pages[3]["items"] == []
    assert (await search(world, "кварцоснимок"))["total"] == 1
    async with world.sessions() as db:
        assert (await db.get(KnowledgeProjectFileText, image_id)).status == "disabled"


async def test_task_caption_keeps_extracted_cache_and_does_not_requeue_vision(world):
    attachment_id = await add_file(world, attachment=True)
    base = worker(world)
    assert await base.process_next()
    async with world.sessions() as db:
        before = await db.get(KnowledgeAttachmentText, attachment_id)
        original_text, original_hash = before.text, before.content_hash
        await db.execute(
            update(TaskAttachment)
            .where(TaskAttachment.id == attachment_id)
            .values(caption="Новая бериллоподпись")
        )
        await db.commit()
        await db.refresh(before)
        assert before.text == original_text and before.content_hash == original_hash
        job = (
            await db.execute(
                select(FileProcessingJob).where(FileProcessingJob.attachment_id == attachment_id)
            )
        ).scalar_one()
        assert job.status == "ready" and job.requested_version == 1
    assert not await base.process_next()
    assert (await search(world, "бериллоподпись"))["total"] == 1
    async with world.sessions() as db:
        assert await FileProcessingJobsRepository(db).request_retry(attachment_id=attachment_id)
        await db.commit()
    assert await base.process_next()
    assert (await search(world, "Извлечённые"))["total"] == 1


async def test_task_move_during_parse_persists_only_current_project_context(world):
    attachment_id = await add_file(world, attachment=True)
    base = worker(world)
    claimed = await base.service.claim_next()
    result = await base.service.extract(claimed)
    async with world.sessions() as db:
        await db.execute(
            update(Task)
            .where(Task.id == world.task)
            .values(project_id=world.other_project, stage_id=world.other_stage)
        )
        await db.commit()
    assert await base.service.persist(claimed, result)
    assert (await search(world, "Извлечённые"))["total"] == 0
    current = await search(
        world, "Извлечённые", user_id=world.stranger, project_id=world.other_project
    )
    assert current["total"] == 1 and current["items"][0]["source_id"] == attachment_id
    assert current["items"][0]["task_key"].endswith("-1")


async def test_extraction_retries_are_bounded_and_independent_from_semantic_queue(world):
    class UnavailableVision:
        calls = 0

        async def extract_image_text(self, *, filename, content):
            self.calls += 1
            raise VisionClientError("synthetic upstream unavailable")

    file_id = await add_file(world, name="ошибка.png", content=b"image")
    vision = UnavailableVision()
    base = worker(world, vision, max_attempts=2)
    assert await base.process_next()
    assert await base.process_next()
    assert not await base.process_next()
    assert vision.calls == 2
    async with world.sessions() as db:
        job = (
            await db.execute(
                select(FileProcessingJob).where(FileProcessingJob.project_file_id == file_id)
            )
        ).scalar_one()
        assert job.status == "failed" and job.attempts == 2
        assert (await db.get(KnowledgeProjectFileText, file_id)).status == "failed"
        assert await FileProcessingJobsRepository(db).request_retry(project_file_id=file_id)
        await db.commit()
        await db.refresh(job)
        assert job.status == "pending" and job.attempts == 0 and job.requested_version == 2
    assert (await search(world, "ошибка"))["total"] == 1


async def test_changed_file_size_is_rejected_before_vision(world):
    file_id = await add_file(world, name="размер.png", content=b"image")
    vision = BlockingVision()
    base = worker(world, vision)
    claimed = await base.service.claim_next()
    world.storage.resolve(claimed.snapshot["storage_key"]).write_bytes(b"image unexpectedly grew")
    extracted = await base.service.extract(claimed)
    assert extracted.status == "failed" and vision.calls == 0
    assert await base.service.persist(claimed, extracted)
    assert (await search(world, "размер"))["items"][0]["source_id"] == file_id
