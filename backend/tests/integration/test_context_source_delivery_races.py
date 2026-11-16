"""Final gates: committed SQL changes during remote waits and unverified previews."""

# ruff: noqa: F811 -- pytest внедряет импортированные фикстуры по имени параметра.

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select, update

from src.core.settings import get_settings
from src.db.models import AgentToolRun, ChatAgentTurn, ProjectMember, ProjectStage, Task
from src.db.models.documents import Document
from src.db.models.task_participants import TaskParticipant
from src.dependencies.agent_context import build_context_evidence_service
from src.exceptions.access import AccessServiceError
from src.exceptions.agent_tools import AgentToolAccessError
from src.exceptions.document_catalog import CatalogDocumentNotFoundError
from src.knowledge.catalog import build_catalog
from src.schemas.agent_conversations import AgentMessageCreateSchema
from src.schemas.git_sources import GitSourceProof
from src.schemas.knowledge import KnowledgeAnswerSchema, encode_answer_sources
from src.schemas.project_chats import ChatMessageCreate
from src.services.context_evidence import ContextEvidence, ContextEvidenceService
from src.services.context_results import ContextReadResult
from src.utils.document_refs import encode_document_ref
from tests.integration.repositories.test_agent_conversations_repository import (
    agent_env as agent_env,
)
from tests.integration.repositories.test_agent_project_actions import actions_env as actions_env
from tests.integration.repositories.test_agent_project_actions import invoke
from tests.integration.repositories.test_agent_project_actions import task as create_task
from tests.integration.repositories.test_context_actions import context_resources
from tests.integration.repositories.test_project_knowledge import map_environment


@pytest.mark.parametrize("material", ["document", "task"])
@pytest.mark.parametrize("change", ["revoke", "edit"])
async def test_personal_material_is_rechecked_after_remote_validation_without_handles(
    engine, monkeypatch, material, change
):
    async with map_environment(engine) as env:
        verifier = build_context_evidence_service(
            session_factory=env.factory, settings=get_settings()
        )
        async with env.factory() as db:
            task = Task(
                project_id=None,
                stage_id=None,
                status="NEW",
                number=int(uuid4().hex[:7], 16),
                title="PRIVATE_PERSONAL_TASK_CONTROL",
            )
            db.add(task)
            await db.flush()
            db.add(TaskParticipant(task_id=task.id, user_id=env.owner_id, role="REPORTER"))
            document = Document(
                owner_task_id=task.id,
                slug=uuid4().hex,
                title="PRIVATE_PERSONAL_DOC_CONTROL",
                content_md="PRIVATE_BODY_CONTROL",
                authored_by_user_id=env.owner_id,
            )
            db.add(document)
            await db.commit()
            task_id, document_id = task.id, document.id
        try:
            ledger = ContextEvidence()
            document_ref = encode_document_ref("document", document_id)
            if material == "document":
                ledger.document(
                    await verifier.documents.get(
                        document_ref=document_ref, user_id=env.owner_id, principal=env.principal
                    )
                )
            else:
                async with verifier.task_scope() as db:
                    ledger.task(await db.tasks.get_by_id(task_id))
            # Начальное право и версия подтверждены настоящими общими SQL-сервисами.
            await verifier.validate(ledger.saved, principal=env.principal)
            proof = GitSourceProof(
                source_id="git_file:6",
                entity_id=6,
                file_id=6,
                project_id=env.project_id,
                connection_id=2,
                repository_id=101,
                ref="main",
                commit_sha="a" * 40,
                path="src/service.py",
                blob_sha="b" * 40,
                text_hash="c" * 64,
                generation=3,
                credential_revision=2,
            )
            ledger.register(ContextReadResult({}, project_id=env.project_id, git_proofs=(proof,)))
            callback_ran = False

            async def change_during_git_wait(**_):
                nonlocal callback_ran
                async with env.factory() as db:
                    if change == "revoke":
                        await db.execute(
                            delete(TaskParticipant).where(
                                TaskParticipant.task_id == task_id,
                                TaskParticipant.user_id == env.owner_id,
                            )
                        )
                    elif material == "document":
                        await db.execute(
                            update(Document)
                            .where(Document.id == document_id)
                            .values(content_md="Changed body", version=Document.version + 1)
                        )
                    else:
                        await db.execute(
                            update(Task)
                            .where(Task.id == task_id)
                            .values(title="Changed task", revision=Task.revision + 1)
                        )
                    await db.commit()
                callback_ran = True

            monkeypatch.setattr(verifier.git, "validate_sources", change_during_git_wait)
            answer = KnowledgeAnswerSchema(answer="Paraphrase of PRIVATE_BODY_CONTROL", sources=[])
            answer._context_evidence = ledger, env.principal
            public, available = await verifier.present(
                encode_answer_sources(answer), user_id=env.owner_id, principal=env.principal
            )
            assert callback_ran
            if change == "revoke":
                if material == "document":
                    with pytest.raises(CatalogDocumentNotFoundError):
                        await verifier.documents.get(
                            document_ref=document_ref, user_id=env.owner_id, principal=env.principal
                        )
                else:
                    async with verifier.task_scope() as db:
                        with pytest.raises(AccessServiceError):
                            await db.access.ensure_task_access(
                                task_id=task_id, user_id=env.owner_id
                            )
            assert not available and not any(public.values())
        finally:
            async with env.factory() as db:
                await db.execute(delete(Task).where(Task.id == task_id))
                await db.commit()


@pytest.mark.parametrize("status", ["processing", "failed", "completed"])
async def test_private_unverified_business_preview_is_hidden_and_pending_acl_and_storage_are_preserved(
    actions_env, status
):
    env = actions_env
    task = await create_task(env, title="PRIVATE_TASK_TITLE_FROM_PROJECT")
    conversation = await env.service.create_conversation(user_id=1)
    accepted = await env.service.send_message(
        user_id=1,
        conversation_id=conversation.id,
        data=AgentMessageCreateSchema(content="Проверь возможность удаления", request_id=uuid4()),
    )
    async with env.service._scope() as db:
        message = await db.messages.claim_next(uuid4())
        await db.unit_of_work.commit()
    assert message.id == accepted.assistant_message.id
    pending = await invoke(
        env,
        "telos_delete_task",
        {"task_id": task["id"], "expected_updated_at": task["updated_at"]},
        context=replace(env.context, message_id=message.id, run_id=message.run_id),
    )
    action_id = UUID(pending["action"]["id"])
    assert "PRIVATE_TASK_TITLE_FROM_PROJECT" in str(
        (await env.actions.get_pending(user_id=1, action_id=action_id)).model_dump()
    )
    if status == "failed":
        await env.service._finish(message, error="Sources changed before final publication")
    elif status == "completed":
        await env.service._finish(
            message, answer=KnowledgeAnswerSchema(answer="Непроверенный прежний ответ", sources=[])
        )
    async with env.factory() as db:
        await db.execute(
            delete(ProjectMember).where(ProjectMember.project_id == 1, ProjectMember.user_id == 1)
        )
        await db.commit()
    with pytest.raises(AgentToolAccessError):
        await env.actions.get_pending(user_id=1, action_id=action_id)
    page = await env.service.get_messages(
        user_id=1, conversation_id=conversation.id, before_id=None, limit=20
    )
    assert page.items[-1].status == status and not page.items[-1].actions
    assert "PRIVATE_TASK_TITLE_FROM_PROJECT" not in str(page.model_dump())
    async with env.factory() as db:
        row = await db.get(AgentToolRun, action_id)
        assert row.status == "pending" and row.result == pending["action"]["result"]
        assert (await db.get(Task, task["id"])).title == task["title"]


@pytest.mark.parametrize("status", ["processing", "failed", "completed"])
async def test_shared_preview_requires_completed_guarded_reply_before_delivery(chat_env, status):
    env = chat_env
    verifier = build_context_evidence_service(session_factory=env.factory, settings=get_settings())
    env.service.presenter.evidence = verifier
    async with env.factory() as db:
        stage = ProjectStage(project_id=1, name="Бэклог preview", color="#334455")
        db.add(stage)
        await db.flush()
        task = Task(project_id=1, stage_id=stage.id, number=123, title="PRIVATE_PREVIEW_CONTROL")
        db.add(task)
        await db.commit()
        task_id = task.id
    await env.service.send(
        1, 1, ChatMessageCreate(content="Арис, проверь задачу", client_message_id=uuid4())
    )
    async with verifier.scope() as db:
        source = build_catalog(1, await db.sources.get_project_rows(1)).sources[f"task:{task_id}"]
    ledger = ContextEvidence()
    ledger.internal(1, source)
    answer = KnowledgeAnswerSchema(answer="Подтвердите явное действие", sources=[])
    answer._context_evidence = ledger, verifier.session_principal(1)
    async with env.factory() as db:
        turn = (await db.scalars(select(ChatAgentTurn))).one()
        turn.status = status
        turn.sources = encode_answer_sources(answer) if status == "completed" else []
        action = AgentToolRun(
            id=uuid4(),
            project_id=1,
            user_id=1,
            chat_turn_id=turn.id,
            request_id=uuid4(),
            tool_name="telos_delete_task",
            title="Удаление задачи",
            arguments={"task_id": task_id},
            status="pending",
            result={"preview": {"title": "PRIVATE_PREVIEW_CONTROL"}},
        )
        db.add(action)
        await db.commit()
        action_id = action.id
    events = await env.service.events(1, 2, after=0)
    assert "PRIVATE_PREVIEW_CONTROL" not in str(events.model_dump())
    reply = (await env.service.history(1, 2)).messages[-1]
    if status == "completed":
        assert reply.agent.actions[0].result["preview"]["title"] == "PRIVATE_PREVIEW_CONTROL"
    else:
        assert not reply.agent.actions and "PRIVATE_PREVIEW_CONTROL" not in str(reply.model_dump())
    async with env.factory() as db:
        assert (await db.get(AgentToolRun, action_id)).result["preview"][
            "title"
        ] == "PRIVATE_PREVIEW_CONTROL"


async def test_completed_source_free_external_consent_stays_visible_and_read_initiator_can_approve(
    actions_env,
):
    env = actions_env
    tools, client, _ = context_resources(env)
    env.responder.context_tools = tools
    actor = replace(
        env.context,
        user_id=2,
        can_write=False,
        principal=ContextEvidenceService.session_principal(2),
    )
    conversation = await env.service.create_conversation(user_id=2)
    await env.service.send_message(
        user_id=2,
        conversation_id=conversation.id,
        data=AgentMessageCreateSchema(
            content="Найди общедоступную документацию Python", request_id=uuid4()
        ),
    )
    async with env.service._scope() as db:
        message = await db.messages.claim_next(uuid4())
        await db.unit_of_work.commit()
    pending = await invoke(
        env,
        "telos_request_web_search",
        {"public_topic": "Python asyncio cancellation"},
        context=replace(actor, message_id=message.id, run_id=message.run_id),
    )
    await env.service._finish(
        message, answer=KnowledgeAnswerSchema(answer="Разрешите публичную тему", sources=[])
    )
    view = (
        await env.service.get_messages(
            user_id=2, conversation_id=conversation.id, before_id=None, limit=20
        )
    ).items[-1]
    assert (
        view.content == "Разрешите публичную тему" and view.actions[0].effect == "external_release"
    )
    assert view.actions[0].arguments == {"public_topic": "Python asyncio cancellation"}
    approved = await env.actions.decide(
        context=replace(actor, project_id=None),
        conversation_id=conversation.id,
        action_id=UUID(pending["action"]["id"]),
        decision="approve",
    )
    assert approved.status == "completed" and approved.effect == "external_release"
    client.read.assert_not_awaited()
    client.search.assert_not_awaited()
