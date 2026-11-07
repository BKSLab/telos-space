"""Локальные thumbnails: bounded decode, безопасный формат и изоляция пути."""

from io import BytesIO

import pytest
from PIL import Image

from src.exceptions.storage import TaskAttachmentStorageError
from src.storage.task_attachments import TaskAttachmentStorage


async def test_preview_is_bounded_jpeg_original_stays_unchanged_and_cleanup_removes_sidecar(
    tmp_path,
):
    storage = TaskAttachmentStorage(tmp_path)
    content = BytesIO()
    image = Image.new("RGB", (1500, 1000), "#123456")
    exif = Image.Exif()
    exif[270] = "Private source metadata"
    image.save(content, "JPEG", exif=exif)
    original = content.getvalue()
    key = await storage.save(1, ".jpg", original)
    preview = await storage.prepare_preview(key)
    assert preview is not None
    with Image.open(preview) as result:
        assert result.format == "JPEG" and max(result.size) == storage.PREVIEW_SIZE[0]
        assert not result.getexif()
    assert storage.resolve(key).read_bytes() == original
    assert await storage.prepare_preview(key) == preview
    await storage.delete_if_exists(key)
    assert not preview.exists() and not [item for item in tmp_path.rglob("*") if item.is_file()]


async def test_preview_rejects_active_corrupt_huge_and_outside_originals(tmp_path):
    storage = TaskAttachmentStorage(tmp_path)
    for content in (
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b"not a raster",
        b"\x89PNG\r\n\x1a\n",
    ):
        key = await storage.save(1, ".png", content)
        assert await storage.prepare_preview(key) is None
    image = BytesIO()
    Image.new("RGB", (200, 200)).save(image, "PNG")
    key = await storage.save(1, ".png", image.getvalue())
    storage.PREVIEW_MAX_PIXELS = 10_000
    assert await storage.prepare_preview(key) is None
    for invalid in ("../outside.png", "/outside.png", "tasks/1/missing.png"):
        with pytest.raises(TaskAttachmentStorageError):
            await storage.prepare_preview(invalid)
    assert not list(tmp_path.rglob("*.preview.jpg"))
