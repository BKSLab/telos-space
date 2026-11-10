"""Все источники, связи и FTS проверяются на настоящем PostgreSQL."""

from datetime import date

from sqlalchemy import text

from src.db.models import (
    AnalyticsReport,
    BoardSticker,
    BoardStickerTaskLink,
    Document,
    DocumentLink,
    ProjectDeadlineChange,
    ProjectFile,
    ProjectMember,
    ProjectMilestone,
    ProjectRisk,
    ProjectStage,
    Task,
    TaskActivity,
    TaskAttachment,
    TaskComment,
    TaskDependency,
    TaskParticipant,
    TaskReport,
    WbsNode,
)
from src.knowledge.catalog import EXCLUDED_TABLES, POLICIES, SourceType, build_catalog
from src.repositories.knowledge_sources import KnowledgeSourcesRepository


async def test_every_source_and_horizontal_relation_is_searchable(db_session, project, user, board):
    member = ProjectMember(project_id=project.id, user_id=user.id, role="OWNER")
    stage = ProjectStage(
        project_id=project.id,
        name="Согласование",
        color="#334455",
        order_index=0,
        is_done_stage=False,
    )
    node = WbsNode(project_id=project.id, title="Пустой раздел инфраструктуры", position=0)
    db_session.add_all([member, stage, node])
    await db_session.flush()
    task = Task(
        project_id=project.id,
        stage_id=stage.id,
        number=1,
        title="Приёмка",
        position=0,
        checklist={
            "title": "Контроль",
            "items": [
                {
                    "id": "00000000-0000-4000-8000-000000000001",
                    "text": "Сверить зебропакет",
                    "is_completed": False,
                }
            ],
        },
    )
    successor = Task(project_id=project.id, stage_id=stage.id, number=2, title="Выпуск", position=1)
    db_session.add_all([task, successor])
    await db_session.flush()
    attachment = TaskAttachment(
        task_id=task.id,
        original_name="Архитектура.txt",
        storage_key="private/path.txt",
        content_type="text/plain",
        size=12,
    )
    sticker = BoardSticker(
        project_id=project.id,
        board_id=board.id,
        body="Стикер: согласовали кварцопоставку",
        created_by_user_id=user.id,
        created_by_username_snapshot=user.username,
        created_by_display_name_snapshot="Владельцев Виктор",
    )
    document = Document(
        project_id=project.id,
        slug="design",
        title="Проектирование",
        content_md="Документ содержит кедросхему",
    )
    risk = ProjectRisk(
        project_id=project.id,
        task_id=task.id,
        title="Риск доставки",
        description="Задержка",
        probability="HIGH",
        impact="HIGH",
        risk_level="HIGH",
        response_strategy="MITIGATE",
        mitigation_plan="Заказать резервный рубинопакет",
        response_plan="Переключиться на резерв",
        owner_user_id=user.id,
    )
    db_session.add_all([attachment, sticker, document, risk])
    project_file = ProjectFile(
        project_id=project.id,
        uploaded_by_user_id=user.id,
        original_name="Материалы.txt",
        storage_key="projects/source.txt",
        content_type="text/plain",
        size=30,
        content_hash="b" * 64,
    )
    db_session.add(project_file)
    await db_session.flush()
    db_session.add_all(
        [
            TaskComment(
                task_id=task.id,
                author_name="Виктор",
                body_md="В обсуждении решили проверить янтарномодуль",
            ),
            TaskReport(
                task_id=task.id,
                revision=1,
                body_md="Отчёт: выполнили аметистовыверку",
                author_id=user.id,
                author_name="Виктор",
            ),
            DocumentLink(document_id=document.id, task_id=task.id),
            BoardStickerTaskLink(sticker_id=sticker.id, task_id=task.id),
            TaskParticipant(task_id=task.id, project_member_id=member.id, role="OBSERVER"),
            TaskDependency(
                project_id=project.id,
                predecessor_task_id=task.id,
                successor_task_id=successor.id,
                dependency_type="FINISH_TO_START",
                lag_days=2,
            ),
            TaskActivity(
                task_id=task.id,
                event_type="DESCRIPTION_CHANGED",
                from_value="Заготовка",
                to_value="Приёмка",
            ),
            ProjectMilestone(
                project_id=project.id,
                title="Первый релиз",
                due_date=date(2026, 12, 1),
                wbs_node_id=node.id,
            ),
            ProjectDeadlineChange(
                project_id=project.id,
                previous_due_date=date(2026, 10, 1),
                new_due_date=date(2026, 12, 1),
                changed_by_user_id=user.id,
                changed_by_name="Виктор",
                comment="Ждём сапфирооборудование",
            ),
            AnalyticsReport(
                project_id=project.id,
                created_by_user_id=user.id,
                created_by_display_name_snapshot="Виктор",
                llm_model="test",
                duration_ms=1,
                payload={"summary": "Свод: агатопроверка"},
                context_summary={},
            ),
            AnalyticsReport(
                project_id=None,
                created_by_user_id=user.id,
                created_by_display_name_snapshot="Виктор",
                llm_model="test",
                duration_ms=1,
                payload={"summary": "Чужой портфель: секретопортфель"},
                context_summary={},
            ),
        ]
    )
    await db_session.flush()
    repository = KnowledgeSourcesRepository(db_session)
    await repository.save_project_file_extraction(
        {
            "project_file_id": project_file.id,
            "text": "Файл проекта: лазуритопоставка",
            "status": "ready",
            "detail": None,
            "original_chars": 30,
            "content_hash": "b" * 64,
        }
    )
    await repository.save_extraction(
        {
            "attachment_id": attachment.id,
            "text": "Файл: обсидианокомпонент",
            "status": "ready",
            "detail": None,
            "original_chars": 26,
            "content_hash": "a" * 64,
        }
    )
    catalog = build_catalog(project.id, await repository.get_project_rows(project.id))
    assert {source.kind for source in catalog.sources.values()} == set(SourceType)
    for query, kind in [
        (project.name, "project"),
        (stage.name, "stage"),
        (node.title, "wbs_node"),
        ("Первый релиз", "milestone"),
        ("Заготовка", "activity"),
        ("зебропакет", "task"),
        ("кварцопоставку", "sticker"),
        ("кедросхему", "document"),
        ("рубинопакет", "risk"),
        ("янтарномодуль", "comment"),
        ("аметистовыверку", "task_report"),
        ("сапфирооборудование", "deadline_change"),
        ("агатопроверка", "analytics_report"),
        ("обсидианокомпонент", "attachment"),
        ("лазуритопоставка", "project_file"),
        ("Владельцев", "member"),
    ]:
        hits = await repository.search(project.id, query)
        assert any(hit["source_id"].startswith(kind + ":") for hit in hits), (query, hits)
    assert not await repository.search(project.id, "секретопортфель")
    links = catalog.sources[f"task:{task.id}"].relations
    assert {link["relation"] for link in links} >= {
        "has_risk",
        "has_document",
        "has_sticker",
        "participant",
        "precedes",
        "contains",
        "in_stage",
    }
    assert any(link.get("role") == "OBSERVER" for link in links)
    assert any(link.get("lag_days") == 2 for link in links)
    assert catalog.sources[f"sticker:{sticker.id}"].payload()["task_ids"] == [str(task.id)]
    assert catalog.sources[f"sticker:{sticker.id}"].payload()["owner_task_id"] is None
    assert "password_hash" not in str(catalog.sources)
    assert "private/path.txt" not in str(catalog.sources)
    assert (
        await db_session.scalar(
            text(
                "SELECT count(*) FROM knowledge_index_jobs WHERE project_id=:pid AND transaction_id=txid_current()"
            ),
            {"pid": project.id},
        )
        == 1
    )
    # Триггеры работают и при bulk UPDATE, а изменение остаётся в той же транзакции.
    await db_session.execute(
        text("UPDATE project_stickers SET body='Новая договорённость' WHERE id=:id"),
        {"id": sticker.id},
    )
    assert (
        "Новая договорённость"
        in build_catalog(project.id, await repository.get_project_rows(project.id))
        .sources[f"sticker:{sticker.id}"]
        .text
    )


def test_model_coverage_requires_an_explicit_policy():
    from src.db.models import Base
    from src.knowledge.catalog import EXCLUDED_FIELDS

    assert set(Base.metadata.tables) == {rule.table for rule in POLICIES} | set(EXCLUDED_TABLES)
    assert {rule.kind for rule in POLICIES if rule.kind} == set(SourceType)
    for rule in POLICIES:
        assert set(Base.metadata.tables[rule.table].c.keys()) == set(rule.fields) | set(
            EXCLUDED_FIELDS.get(rule.table, {})
        ), rule.table


async def test_fts_reads_large_file_tail_without_postgres_vector_overflow(
    db_session, project, stage
):
    task = Task(
        project_id=project.id, stage_id=stage.id, number=1, title="Большой файл", position=1000
    )
    db_session.add(task)
    await db_session.flush()
    attachment = TaskAttachment(
        task_id=task.id,
        original_name="large.txt",
        storage_key="large",
        content_type="text/plain",
        size=1000000,
    )
    db_session.add(attachment)
    await db_session.flush()
    repository = KnowledgeSourcesRepository(db_session)
    full_text = " ".join(f"термин{number:06d}" for number in range(65000))
    await repository.save_extraction(
        {
            "attachment_id": attachment.id,
            "text": full_text,
            "status": "ready",
            "detail": None,
            "original_chars": len(full_text),
            "content_hash": "test",
        }
    )
    hits = await repository.search(project.id, "термин064999")
    assert [hit["source_id"] for hit in hits] == [f"attachment:{attachment.id}"]
