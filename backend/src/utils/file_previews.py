"""Безопасные кандидаты для локального растрового preview."""

from pathlib import PurePosixPath

RASTER_EXTENSIONS = frozenset({".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"})


def is_raster_previewable(name: str, content_type: str) -> bool:
    """SVG/HTML и прочие активные форматы никогда не выдаются миниатюрами."""
    return PurePosixPath(name).suffix.lower() in RASTER_EXTENSIONS and content_type.startswith(
        "image/"
    )
