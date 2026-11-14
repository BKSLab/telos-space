"""Хранилище файлов чата с пакетной уборкой после оборванных загрузок."""

from src.storage.task_attachments import TaskAttachmentStorage


class ChatAttachmentStorage(TaskAttachmentStorage):
    """Переиспользует атомарную запись и защищённое разрешение storage key."""

    async def delete(self, storage_key: str) -> None:
        """Идемпотентно удаляет файл при конкурентной уборке экземплярами."""
        await self.delete_if_exists(storage_key)
