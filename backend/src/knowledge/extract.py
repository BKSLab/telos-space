from __future__ import annotations

import asyncio
import logging
from io import BytesIO
from pathlib import Path

import chardet
import docx
from pdfminer.high_level import extract_text as extract_pdf_text
from pdfminer.pdfpage import PDFPage

from src.clients.vision import VisionCapability
from src.knowledge.excel import extract_excel_text
from src.knowledge.images import IMAGE_MEDIA_TYPES

logger = logging.getLogger(__name__)

PLAIN_TEXT_EXTENSIONS = frozenset({".csv", ".log", ".md", ".txt"})
EXCEL_EXTENSIONS = frozenset({".xls", ".xlsm", ".xlsx"})
IMAGE_EXTENSIONS = frozenset(IMAGE_MEDIA_TYPES)
INDEXABLE_EXTENSIONS = (
    PLAIN_TEXT_EXTENSIONS | EXCEL_EXTENSIONS | IMAGE_EXTENSIONS | {".docx", ".pdf"}
)
MAX_PDF_PAGES = 2000
MIN_ENCODING_CONFIDENCE = 0.7
LEGACY_TEXT_ENCODING = "cp1251"


async def extract_indexable_text(
    filename: str,
    content: bytes,
    *,
    vision: VisionCapability,
    max_chars: int | None = None,
) -> str | None:
    """Извлекает текст поддерживаемого вложения; остальные типы пропускает.

    Разбор книг и документов идёт в отдельном потоке, изображения уходят в
    vision-модель: у них нет текстового слоя, распознать содержимое можно
    только моделью.

    Args:
        filename: Исходное имя файла, по расширению которого выбирается разбор.
        content: Бинарное содержимое файла.
        vision: Способность распознать изображение. Выключенное
            распознавание передаётся явной no-op реализацией, а не ``None``.
        max_chars: Предел длины результата, если он задан настройками.

    Returns:
        Извлечённый текст либо None, если тип файла не индексируется.

    Raises:
        ValueError: Если файл повреждён или превышает ограничения разбора.
        KnowledgeProviderError: Если vision-модель недоступна.
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in INDEXABLE_EXTENSIONS:
        return None
    if suffix in IMAGE_EXTENSIONS:
        text = await vision.extract_image_text(filename=filename, content=content)
    else:
        text = await asyncio.to_thread(_extract_binary_text, suffix, content)
    if text is None:
        return None
    # PostgreSQL text/JSONB не допускают U+0000. Пробел сохраняет границы слов,
    # а исходные байты оригинала продолжают храниться без преобразования.
    return _limit(text.replace("\x00", " ").strip(), max_chars=max_chars, filename=filename)


def _extract_binary_text(suffix: str, content: bytes) -> str:
    """Разбирает форматы, читаемые без обращения к внешним сервисам."""
    if suffix in PLAIN_TEXT_EXTENSIONS:
        return _decode_text(content)
    if suffix in EXCEL_EXTENSIONS:
        return extract_excel_text(content)
    if suffix == ".pdf":
        return _extract_pdf(content)
    return _extract_docx(content)


def _decode_text(content: bytes) -> str:
    """Декодирует простой текст, подбирая кодировку от точного к вероятному.

    Выгрузки CSV и логи в проектах нередко приходят в cp1251, и жёсткий utf-8
    превратил бы их в нечитаемый для эмбеддингов мусор. Порядок важен: utf-8
    проверяется первым, потому что он самоконтролируем и ошибиться не даёт,
    а chardet путает однобайтовые кириллические кодировки между собой, поэтому
    его догадка принимается только уверенной, и последним словом остаётся
    cp1251 — доминирующая legacy-кодировка русских выгрузок.
    """
    for encoding in _candidate_encodings(content):
        try:
            return content.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode(LEGACY_TEXT_ENCODING, errors="replace")


def _candidate_encodings(content: bytes) -> list[str]:
    """Перечисляет кодировки-кандидаты в порядке убывания надёжности."""
    detected = chardet.detect(content)
    encoding = detected.get("encoding")
    confidence = detected.get("confidence") or 0.0
    if encoding and confidence >= MIN_ENCODING_CONFIDENCE:
        return ["utf-8-sig", encoding, LEGACY_TEXT_ENCODING]
    return ["utf-8-sig", LEGACY_TEXT_ENCODING]


def _extract_pdf(content: bytes) -> str:
    """Извлекает текстовый слой PDF постранично."""
    stream = BytesIO(content)
    page_count = sum(
        1
        for _ in PDFPage.get_pages(
            stream,
            maxpages=MAX_PDF_PAGES + 1,
            check_extractable=False,
        )
    )
    if page_count > MAX_PDF_PAGES:
        raise ValueError(f"PDF содержит больше {MAX_PDF_PAGES} страниц.")
    stream.seek(0)
    # pdfminer разделяет страницы form-feed; наружу отдаём обычные текстовые блоки.
    return extract_pdf_text(stream).replace("\x0c", "\n\n")


def _extract_docx(content: bytes) -> str:
    """Извлекает параграфы и таблицы DOCX в порядке XML-блоков."""
    document = docx.Document(BytesIO(content))
    blocks: list[str] = []
    for block in document.element.body.iterchildren():
        tag = block.tag.split("}")[-1]
        if tag == "p":
            paragraph = docx.text.paragraph.Paragraph(block, document)
            text = paragraph.text.strip()
            if text:
                blocks.append(text)
        elif tag == "tbl":
            table = docx.table.Table(block, document)
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    blocks.append(" | ".join(cells))
    return "\n".join(blocks)


def _limit(text: str, *, max_chars: int | None, filename: str) -> str:
    """Обрезает слишком длинный текст, чтобы одно вложение не залило индекс."""
    if max_chars is None or len(text) <= max_chars:
        return text
    logger.warning(
        "⚠️ Текст вложения %s обрезан с %s до %s символов.",
        filename,
        len(text),
        max_chars,
    )
    return text[:max_chars]
