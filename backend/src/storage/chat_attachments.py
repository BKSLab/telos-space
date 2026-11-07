"""Хранилище файлов чата с пакетной уборкой после оборванных загрузок."""

import asyncio
from itertools import islice

from src.exceptions.storage import TaskAttachmentStorageError
from src.storage.task_attachments import TaskAttachmentStorage


class ChatAttachmentStorage(TaskAttachmentStorage):
    """Переиспользует атомарную запись и защищённое разрешение storage key."""

    async def delete(self, storage_key: str) -> None:
        """Идемпотентно удаляет файл при конкурентной уборке экземплярами."""
        await self.delete_if_exists(storage_key)

    async def old_key_batches(self, cutoff_timestamp: float):
        """Обходит старые файлы вне event loop и без загрузки всего списка в память."""

        def keys():
            root = self.root.resolve()
            for path in root.rglob("*"):
                try:
                    if path.is_file() and path.stat().st_mtime < cutoff_timestamp:
                        yield path.relative_to(root).as_posix()
                except FileNotFoundError:
                    continue

        candidates = keys()
        try:
            while batch := await asyncio.to_thread(lambda: list(islice(candidates, 100))):
                yield batch
        except OSError as error:
            raise TaskAttachmentStorageError(str(error)) from error
