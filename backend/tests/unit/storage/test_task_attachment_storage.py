from pathlib import Path, PurePosixPath

import pytest

from src.exceptions.storage import TaskAttachmentStorageError
from src.storage.task_attachments import TaskAttachmentStorage


@pytest.mark.asyncio
async def test_save_resolve_and_delete_file(tmp_path: Path) -> None:
    storage = TaskAttachmentStorage(root=tmp_path / "uploads")

    storage_key = await storage.save(task_id=12, extension=".pdf", content=b"content")
    stored_path = storage.resolve(storage_key)

    assert storage_key.startswith("tasks/12/")
    assert stored_path.suffix == ".pdf"
    assert stored_path.read_bytes() == b"content"

    await storage.delete(storage_key)

    assert stored_path.exists() is False


def test_resolve_rejects_path_traversal(tmp_path: Path) -> None:
    storage = TaskAttachmentStorage(root=tmp_path / "uploads")

    with pytest.raises(TaskAttachmentStorageError):
        storage.resolve("../outside.pdf")


@pytest.mark.parametrize("directory", ["../outside", "/outside", "projects/../../outside"])
async def test_save_in_rejects_escaping_directory(tmp_path: Path, directory: str) -> None:
    storage = TaskAttachmentStorage(root=tmp_path / "uploads")
    with pytest.raises(TaskAttachmentStorageError):
        await storage.save_in(PurePosixPath(directory), ".png", b"content")
    assert not storage.root.exists()


async def test_project_cleanup_is_idempotent_and_preserves_neighbour_files(tmp_path: Path) -> None:
    """Очистка проекта не затрагивает другие проекты, задачи и внешние файлы."""
    storage = TaskAttachmentStorage(tmp_path / "uploads")
    removed = await storage.save_in(PurePosixPath("projects/11/files"), ".txt", b"remove")
    neighbour = await storage.save_in(PurePosixPath("projects/12/files"), ".txt", b"keep")
    task = await storage.save(11, ".txt", b"task")
    await storage.delete_project_file_directory(11)
    await storage.delete_project_file_directory(11)
    assert not (storage.root / removed).exists()
    assert storage.resolve(neighbour).read_bytes() == b"keep"
    assert storage.resolve(task).read_bytes() == b"task"
    await storage.delete_if_exists(neighbour)
    await storage.delete_if_exists(neighbour)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    for key in ("../outside.txt", str(outside.resolve())):
        with pytest.raises(TaskAttachmentStorageError):
            await storage.delete_if_exists(key)
    assert outside.read_bytes() == b"outside"
