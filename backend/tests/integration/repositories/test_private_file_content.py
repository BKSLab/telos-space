"""Приватный preview: настоящий PostgreSQL, права и пул вне файлового IO."""

# ruff: noqa: F811 -- импортированная pytest-фикстура внедряется по имени.

from io import BytesIO
from uuid import uuid4

import httpx
from PIL import Image
from sqlalchemy import delete

from main import app
from src.core.settings import get_settings
from src.db.models.agent_conversations import AgentConversation
from src.db.models.agent_files import AgentFile
from src.db.models.project_members import ProjectMember
from src.dependencies.agent_file_content import (
    build_agent_file_content_service,
    get_agent_file_content_service,
)
from src.storage.task_attachments import TaskAttachmentStorage
from src.utils.tokens import create_access_token
from tests.integration.repositories.test_agent_conversations_repository import (
    agent_env as agent_env,
)


async def test_private_thumbnail_access_and_released_connection(agent_env, tmp_path):
    """Файл не открывается чужому участнику; thumbnails не удерживают пул."""
    settings = get_settings().model_copy(deep=True)
    settings.app.uploads_path = tmp_path
    file_id, other_file_id = uuid4(), uuid4()
    storage = TaskAttachmentStorage(tmp_path / "agent")
    content = BytesIO()
    Image.new("RGB", (2048, 1024), "navy").save(content, format="PNG")
    key = await storage.save(1, ".png", content.getvalue())
    async with agent_env.factory() as session:
        session.add_all(
            [
                AgentConversation(id=101, project_id=1, user_id=1, title="Личный диалог"),
                AgentConversation(id=102, project_id=1, user_id=2, title="Чужой диалог"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                AgentFile(
                    id=identity,
                    conversation_id=conversation_id,
                    original_name="Фото.png",
                    content_type="image/png",
                    size=len(content.getvalue()),
                    storage_key=key,
                )
                for identity, conversation_id in [(file_id, 101), (other_file_id, 102)]
            ]
        )
        await session.commit()
    service = build_agent_file_content_service(session_factory=agent_env.factory, settings=settings)

    class ObservedStorage(TaskAttachmentStorage):
        async def prepare_preview(self, storage_key):
            assert agent_env.engine.pool.checkedout() == 0
            return await super().prepare_preview(storage_key)

    service.storage = ObservedStorage(tmp_path / "agent")
    app.dependency_overrides[get_agent_file_content_service] = lambda: service
    cookie_name = settings.auth.session_cookie_name
    original = f"/api/v1/tim/conversations/101/files/{file_id}"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set(cookie_name, create_access_token(1))
        preview = await client.get(f"{original}/preview")
        assert preview.status_code == 200, preview.text
        assert preview.headers["content-type"] == "image/jpeg"
        assert preview.headers["cache-control"] == "private, no-store"
        with Image.open(BytesIO(preview.content)) as decoded:
            assert max(decoded.size) <= 640
        download = await client.get(f"{original}/content")
        assert download.status_code == 200
        assert download.content == content.getvalue()
        assert download.headers["content-disposition"].startswith("attachment;")
        assert (
            await client.get(f"/api/v1/tim/conversations/101/files/{other_file_id}/preview")
        ).status_code == 404
        client.cookies.set(cookie_name, create_access_token(2))
        assert (await client.get(f"{original}/preview")).status_code == 404
        client.cookies.set(cookie_name, create_access_token(1))
        assert (
            await client.get(f"/api/v1/projects/2/agent/conversations/101/files/{file_id}/preview")
        ).status_code == 404
        async with agent_env.factory() as session:
            await session.execute(
                delete(ProjectMember).where(
                    ProjectMember.project_id == 1, ProjectMember.user_id == 1
                )
            )
            await session.commit()
        assert (await client.get(f"{original}/preview")).status_code == 404
        assert agent_env.engine.pool.checkedout() == 0
