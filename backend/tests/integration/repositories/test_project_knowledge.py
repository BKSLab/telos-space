"""Реальные PostgreSQL generation/outbox и производная карта проекта."""

from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.db.models.api_tokens import ApiTokenScope
from src.db.models.documents import Document
from src.db.models.knowledge_index_jobs import KnowledgeIndexJob
from src.db.models.project_knowledge import (
    ProjectKnowledgeClaim,
    ProjectKnowledgeManifest,
    ProjectKnowledgeSource,
    ProjectKnowledgeSourceVersion,
    ProjectKnowledgeState,
)
from src.db.models.project_members import ProjectMember, ProjectRole
from src.db.models.project_stages import ProjectStage
from src.db.models.projects import Project
from src.db.models.task_reports import TaskReport
from src.db.models.tasks import Task
from src.db.models.users import User
from src.dependencies.project_knowledge import (
    build_project_knowledge_service,
    build_project_knowledge_update_scope,
)
from src.exceptions.project_knowledge import ProjectKnowledgeAccessError, ProjectKnowledgeStaleError
from src.schemas.projects import ProjectDescriptionSchema
from src.services.auth import Principal
from src.services.project_knowledge_updates import ProjectKnowledgeUpdatesService
from src.utils.project_description import compose_project_description


@asynccontextmanager
async def map_environment(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = SimpleNamespace(
        auth=SimpleNamespace(registration_invite_code=SecretStr("unused-test")),
        knowledge=SimpleNamespace(knowledge_enabled=False),
    )
    async with factory() as session:
        owner = User(
            username="map_owner_" + uuid4().hex[:10],
            password_hash="unused",
            first_name="Владелец",
            last_name="Карты",
        )
        member = User(
            username="map_member_" + uuid4().hex[:10],
            password_hash="unused",
            first_name="Участник",
            last_name="Карты",
        )
        session.add_all([owner, member])
        await session.flush()
        sections = ProjectDescriptionSchema(
            goal="Показать доказуемый контекст", constraints="Доступ: локальный"
        )
        project = Project(
            owner_id=owner.id,
            key="P" + uuid4().hex[:8].upper(),
            name="Карта",
            color="#58a6ff",
            context_revision=1,
            description_sections=sections.model_dump(),
            description_md=compose_project_description(sections),
        )
        session.add(project)
        await session.flush()
        session.add_all(
            [
                ProjectMember(project_id=project.id, user_id=owner.id, role=ProjectRole.OWNER),
                ProjectMember(project_id=project.id, user_id=member.id, role=ProjectRole.MEMBER),
            ]
        )
        await session.commit()
        project_id, owner_id, member_id = project.id, owner.id, member.id
        principal = Principal(
            user_id=owner_id,
            username=owner.username,
            first_name=owner.first_name,
            last_name=owner.last_name,
            middle_name=None,
            scope=ApiTokenScope.WRITE,
            via_api_token=False,
        )
        service = build_project_knowledge_service(session_factory=factory, settings=settings)
        updates = ProjectKnowledgeUpdatesService(
            scope=build_project_knowledge_update_scope(session_factory=factory)
        )
        try:
            yield SimpleNamespace(
                project_id=project_id,
                owner_id=owner_id,
                member_id=member_id,
                principal=principal,
                session=session,
                factory=factory,
                service=service,
                updates=updates,
            )
        finally:
            await session.rollback()
            await session.execute(delete(Project).where(Project.id == project_id))
            await session.execute(
                delete(KnowledgeIndexJob).where(KnowledgeIndexJob.project_id == project_id)
            )
            await session.execute(delete(User).where(User.id.in_([owner_id, member_id])))
            await session.commit()


async def test_map_versions_exact_evidence_conflicts_invalidation_delete_acl_and_reuse(engine):
    async with map_environment(engine) as env:
        await env.updates.process_project(env.project_id)
        current = await env.service.read_map(project_id=env.project_id, principal=env.principal)
        assert current.items and current.status == "ready"
        goal = next(item for item in current.items if item.category == "goal")
        assert goal.kind == "requirement" and goal.acceptance == "accepted"
        assert goal.evidence[0].quote == "Показать доказуемый контекст"
        matching = await env.service.read_map(
            project_id=env.project_id, principal=env.principal, category="goal",
            query="ДОКАЗУЕМЫЙ",
        )
        assert matching.total == 1 and matching.items[0].id == goal.id
        assert current.coverage.complete
        assert any(
            gap.code == "passport_field_unknown" and gap.category == "audience"
            for gap in current.gaps
        )
        await env.service.validate_observation(
            project_id=env.project_id, principal=env.principal, observation=current.observation
        )
        await env.updates.process_project(env.project_id)
        reused = await env.service.get_status(project_id=env.project_id, principal=env.principal)
        assert reused.metrics.source_reads == 0 and reused.metrics.sources_reused > 0
        assert reused.metrics.model_requests == reused.metrics.http_attempts == 0
        docs = [
            Document(
                project_id=env.project_id,
                slug="one",
                title="Первый",
                content_md="API: private",
                authored_by_user_id=env.owner_id,
            ),
            Document(
                project_id=env.project_id,
                slug="two",
                title="Второй",
                content_md="API: public",
                authored_by_user_id=env.member_id,
            ),
        ]
        env.session.add_all(docs)
        await env.session.commit()
        doc_id = docs[0].id
        with pytest.raises(ProjectKnowledgeStaleError):
            await env.service.validate_observation(
                project_id=env.project_id, principal=env.principal, observation=current.observation
            )
        await env.updates.process_project(env.project_id)
        current = await env.service.read_map(
            project_id=env.project_id, principal=env.principal, limit=50
        )
        conflicts = [item for item in current.items if item.state == "conflicting"]
        assert len(conflicts) == 2
        assert all(item.acceptance == "unreviewed" and item.conflicts_with for item in conflicts)
        claim = next(item for item in current.items if f"document:{doc_id}" in item.source_ids)
        assert claim.provenance.author_user_id == env.owner_id
        assert claim.evidence[0].start == 0 and claim.evidence[0].end == len("API: private")
        # Commit изменения мгновенно скрывает старую цитату до worker.
        await env.session.execute(
            update(Document)
            .where(Document.id == doc_id)
            .values(content_md="API: updated", version=2)
        )
        await env.session.commit()
        stale = await env.service.get_claim(
            project_id=env.project_id, principal=env.principal, claim_id=claim.id
        )
        assert stale.text is None and not stale.evidence and not stale.source_ids
        hidden = await env.service.read_map(
            project_id=env.project_id, principal=env.principal, query="API: private"
        )
        assert hidden.total == 0 and not hidden.items
        await env.updates.process_project(env.project_id)
        updated = await env.service.get_claim(
            project_id=env.project_id, principal=env.principal, claim_id=claim.id
        )
        assert updated.revision == claim.revision + 1 and "updated" in updated.text
        await env.session.execute(delete(Document).where(Document.id == doc_id))
        await env.session.commit()
        deleted = await env.service.get_claim(
            project_id=env.project_id, principal=env.principal, claim_id=claim.id
        )
        assert deleted.text is None and deleted.provenance.author_user_id is None
        # Участник читает, но refresh владельца не расширяется на member.
        member = replace(env.principal, user_id=env.member_id)
        await env.service.get_status(project_id=env.project_id, principal=member)
        with pytest.raises(ProjectKnowledgeAccessError) as error:
            await env.service.request_refresh(project_id=env.project_id, principal=member)
        assert error.value.status_code == 403
        await env.session.execute(
            delete(ProjectMember).where(
                ProjectMember.project_id == env.project_id, ProjectMember.user_id == env.member_id
            )
        )
        await env.session.commit()
        with pytest.raises(ProjectKnowledgeAccessError) as error:
            await env.service.get_claim(
                project_id=env.project_id, principal=member, claim_id=goal.id
            )
        assert error.value.status_code == 404


async def test_task_report_reopen_new_version_and_no_report_are_not_production_proof(engine):
    async with map_environment(engine) as env:
        active = ProjectStage(
            project_id=env.project_id,
            name="Работа",
            order_index=0,
            color="#334455",
            is_done_stage=False,
        )
        done = ProjectStage(
            project_id=env.project_id,
            name="Принято",
            order_index=1,
            color="#334455",
            is_done_stage=True,
        )
        env.session.add_all([active, done])
        await env.session.flush()
        task = Task(
            project_id=env.project_id, stage_id=done.id, number=1, title="Результат", position=1000
        )
        env.session.add(task)
        await env.session.commit()
        task_id, active_id, done_id = task.id, active.id, done.id
        await env.updates.process_project(env.project_id)
        missing = await env.service.read_map(
            project_id=env.project_id, principal=env.principal, limit=50
        )
        status_only = next(item for item in missing.items if item.category == "task_results")
        assert status_only.verification == "status_only"
        assert any(gap.code == "task_report_missing" for gap in missing.gaps)
        report = TaskReport(
            task_id=task_id,
            revision=1,
            body_md="Участник заявил выкладку",
            author_id=env.member_id,
            author_name="Участник",
        )
        env.session.add(report)
        await env.session.commit()
        await env.updates.process_project(env.project_id)
        mapped = await env.service.read_map(
            project_id=env.project_id, principal=env.principal, category="task_results", limit=50
        )
        claim = next(item for item in mapped.items if item.verification == "participant_report")
        assert claim.acceptance == "unreviewed" and not claim.provenance.independently_verified
        await env.session.execute(update(Task).where(Task.id == task_id).values(stage_id=active_id))
        await env.session.commit()
        assert (
            await env.service.get_claim(
                project_id=env.project_id, principal=env.principal, claim_id=claim.id
            )
        ).text is None
        await env.updates.process_project(env.project_id)
        assert not (
            await env.service.read_map(
                project_id=env.project_id, principal=env.principal, category="task_results"
            )
        ).items
        await env.session.execute(update(Task).where(Task.id == task_id).values(stage_id=done_id))
        env.session.add(
            TaskReport(
                task_id=task_id,
                revision=2,
                body_md="Новая проверка результата",
                author_id=env.owner_id,
                author_name="Владелец",
            )
        )
        await env.session.commit()
        await env.updates.process_project(env.project_id)
        latest = await env.service.read_map(
            project_id=env.project_id, principal=env.principal, category="task_results", limit=50
        )
        assert len(latest.items) == 1 and "Новая проверка" in latest.items[0].text


async def test_checkpoint_restart_and_stale_worker_never_replaces_current_map(engine):
    import time

    async with map_environment(engine) as env:
        document = Document(
            project_id=env.project_id,
            slug="large",
            title="Большой источник",
            content_md="Факт🙂 " * 15_000,
        )
        env.session.add(document)
        await env.session.commit()
        document_id = document.id
        await env.updates.process_project(env.project_id)
        first = await env.service.get_status(project_id=env.project_id, principal=env.principal)
        assert first.status == "partial" and not first.coverage.complete
        cursor = await env.session.scalar(
            select(ProjectKnowledgeSource.processed_chars).where(
                ProjectKnowledgeSource.project_id == env.project_id,
                ProjectKnowledgeSource.source_id == f"document:{document_id}",
            )
        )
        assert 0 < cursor <= 32_000
        # Новый экземпляр сервиса/сессии продолжает сохранённый cursor.
        restarted = ProjectKnowledgeUpdatesService(
            scope=build_project_knowledge_update_scope(session_factory=env.factory)
        )
        await restarted.process_project(env.project_id)
        second_cursor = await env.session.scalar(
            select(ProjectKnowledgeSource.processed_chars).where(
                ProjectKnowledgeSource.project_id == env.project_id,
                ProjectKnowledgeSource.source_id == f"document:{document_id}",
            )
        )
        assert second_cursor > cursor
        await restarted.process_project(env.project_id)
        complete = await env.service.get_status(project_id=env.project_id, principal=env.principal)
        assert complete.coverage.complete and complete.metrics.sources_reused > 0
        # Старый worker получает snapshot, затем другая committed транзакция
        # меняет primary source. CAS не даёт дописать никакую старую revision.
        snapshot = await restarted._snapshot(env.project_id)
        plan = restarted._plan(snapshot, started_at=time.monotonic())
        before = await env.session.scalar(
            select(func.count())
            .select_from(ProjectKnowledgeManifest)
            .where(ProjectKnowledgeManifest.project_id == env.project_id)
        )
        async with env.factory() as writer:
            await writer.execute(
                update(Document)
                .where(Document.id == document_id)
                .values(content_md="Новая версия", version=2)
            )
            await writer.commit()
        with pytest.raises(ProjectKnowledgeStaleError):
            await restarted._publish(plan, started_at=time.monotonic())
        assert (
            await env.session.scalar(
                select(func.count())
                .select_from(ProjectKnowledgeManifest)
                .where(ProjectKnowledgeManifest.project_id == env.project_id)
            )
            == before
        )
        pending = await env.service.read_map(
            project_id=env.project_id, principal=env.principal, limit=50
        )
        assert all(f"document:{document_id}" not in item.source_ids for item in pending.items)
        await restarted.process_project(env.project_id)
        latest = await env.service.read_map(
            project_id=env.project_id, principal=env.principal, limit=50
        )
        assert any("Новая версия" in item.text for item in latest.items)
        # История source versions сохраняется, а тексты в ней не дублируются.
        versions = list(
            (
                await env.session.execute(
                    select(ProjectKnowledgeSourceVersion).where(
                        ProjectKnowledgeSourceVersion.project_id == env.project_id,
                        ProjectKnowledgeSourceVersion.source_id == f"document:{document_id}",
                    )
                )
            ).scalars()
        )
        assert len(versions) == 2 and len({item.content_hash for item in versions}) == 2
        assert not hasattr(versions[0], "text")


async def test_map_publication_rolls_back_history_evidence_and_manifest_together(engine):
    import time
    from unittest.mock import patch

    from src.repositories.project_knowledge import ProjectKnowledgeRepository

    async with map_environment(engine) as env:
        snapshot = await env.updates._snapshot(env.project_id)
        plan = env.updates._plan(snapshot, started_at=time.monotonic())
        with patch.object(
            ProjectKnowledgeRepository,
            "save_manifest",
            side_effect=RuntimeError("controlled publication interruption"),
        ):
            with pytest.raises(RuntimeError, match="controlled publication"):
                await env.updates._publish(plan, started_at=time.monotonic())
        assert (
            await env.session.scalar(
                select(func.count())
                .select_from(ProjectKnowledgeClaim)
                .where(ProjectKnowledgeClaim.project_id == env.project_id)
            )
            == 0
        )
        assert (
            await env.session.scalar(
                select(func.count())
                .select_from(ProjectKnowledgeSourceVersion)
                .where(ProjectKnowledgeSourceVersion.project_id == env.project_id)
            )
            == 0
        )
        await env.updates.process_project(env.project_id)
        assert (
            await env.service.read_map(project_id=env.project_id, principal=env.principal)
        ).items


async def test_source_clock_is_atomic_deduplicated_and_distinguishes_aba(engine) -> None:
    """Две мутации/триггера tx объединяются; rollback и A→B→A честно различаются."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user = User(
            username="map_" + uuid4().hex[:12],
            password_hash="unused",
            first_name="Тест",
            last_name="Карты",
        )
        session.add(user)
        await session.flush()
        project = Project(
            owner_id=user.id,
            key="M" + uuid4().hex[:8].upper(),
            name="Карта",
            color="#58a6ff",
            description_md="A",
        )
        session.add(project)
        await session.commit()
        project_id, user_id = project.id, user.id
        try:

            async def clock():
                return await session.scalar(
                    select(ProjectKnowledgeSource.generation).where(
                        ProjectKnowledgeSource.project_id == project_id,
                        ProjectKnowledgeSource.source_id == f"project:{project_id}",
                    )
                )

            first = await clock()
            await session.execute(
                update(Project).where(Project.id == project_id).values(description_md="B")
            )
            await session.execute(
                update(Project).where(Project.id == project_id).values(name="Уточнённая карта")
            )
            await session.commit()
            second = await clock()
            assert second == first + 1
            await session.execute(
                update(Project).where(Project.id == project_id).values(description_md="A")
            )
            await session.rollback()
            assert await clock() == second
            await session.execute(
                update(Project).where(Project.id == project_id).values(description_md="A")
            )
            await session.commit()
            assert await clock() == second + 1
            assert (
                await session.scalar(select(Project.description_md).where(Project.id == project_id))
                == "A"
            )
            generation = await session.scalar(
                select(ProjectKnowledgeState.generation).where(
                    ProjectKnowledgeState.project_id == project_id
                )
            )
            assert generation == second + 1
            jobs = list(
                (
                    await session.execute(
                        select(KnowledgeIndexJob).where(KnowledgeIndexJob.project_id == project_id)
                    )
                ).scalars()
            )
            assert len(jobs) == 3
        finally:
            await session.execute(delete(Project).where(Project.id == project_id))
            await session.execute(
                delete(KnowledgeIndexJob).where(KnowledgeIndexJob.project_id == project_id)
            )
            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()


async def test_map_nested_conflicts_and_gaps_remain_bounded(engine):
    async with map_environment(engine) as env:
        stage = ProjectStage(
            project_id=env.project_id,
            name="Готово",
            order_index=0,
            color="#334455",
            is_done_stage=True,
        )
        env.session.add(stage)
        await env.session.flush()
        env.session.add_all(
            [
                Document(
                    project_id=env.project_id,
                    slug=f"api-{index}",
                    title=f"Источник {index}",
                    content_md=f"API: значение {index}",
                )
                for index in range(25)
            ]
        )
        env.session.add_all(
            [
                Task(
                    project_id=env.project_id,
                    stage_id=stage.id,
                    number=index + 1,
                    title=f"Без отчёта {index}",
                    position=(index + 1) * 1000,
                )
                for index in range(40)
            ]
        )
        await env.session.commit()
        await env.updates.process_project(env.project_id)
        await env.updates.process_project(env.project_id)
        page = await env.service.read_map(
            project_id=env.project_id, principal=env.principal, limit=50
        )
        assert len(page.gaps) <= 32
        assert any(gap.code == "gaps_truncated" for gap in page.gaps)
        conflicts = [claim for claim in page.items if claim.state == "conflicting"]
        assert len(conflicts) == 25
        assert all(0 < len(claim.conflicts_with) <= 20 for claim in conflicts)
