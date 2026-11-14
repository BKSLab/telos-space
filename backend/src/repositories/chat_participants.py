"""Публичная проекция участников проекта для общего чата."""

from sqlalchemy import select

from src.db.models.project_members import ProjectMember
from src.db.models.users import User
from src.repositories.chat_base import ChatRepository


class ChatParticipantsRepository(ChatRepository):
    """Состав чата не имеет отдельной таблицы членства."""

    async def get_member(self, project_id: int, user_id: int):
        """Проверяет одновременно актуальное участие и активность пользователя."""
        return (
            await self._execute(
                select(User.id)
                .join(ProjectMember, ProjectMember.user_id == User.id)
                .where(
                    ProjectMember.project_id == project_id,
                    User.id == user_id,
                    User.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()

    async def get_members(self, project_id: int):
        """Возвращает имена доступных для упоминания участников."""
        return list(
            (
                await self._execute(
                    select(
                        User.id, User.username, User.first_name, User.last_name,
                        User.avatar_key.is_not(None).label("has_avatar"),
                    )
                    .join(ProjectMember, ProjectMember.user_id == User.id)
                    .where(ProjectMember.project_id == project_id, User.is_active.is_(True))
                    .order_by(User.last_name, User.id)
                )
            ).mappings()
        )

    async def get_users(self, user_ids: list[int]):
        """Читает публичные имена авторов истории, включая бывших участников."""
        return list(
            (
                await self._execute(
                    select(
                        User.id, User.username, User.first_name, User.last_name,
                        User.avatar_key.is_not(None).label("has_avatar"),
                    ).where(
                        User.id.in_(user_ids)
                    )
                )
            ).mappings()
        )
