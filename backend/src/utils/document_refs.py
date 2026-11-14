"""Стабильные ссылки документов, независимые от проекта и задачи."""

import base64
import hashlib
import re

DOCUMENT_SOURCE_KINDS = frozenset({"document", "project_file", "attachment"})


def encode_document_ref(source_kind: str, source_id: int) -> str:
    """Кодирует тип первичного хранилища и ID без сведений о правах доступа."""
    # Первичные ключи всех трёх источников имеют тип PostgreSQL INTEGER.
    if (
        source_kind not in DOCUMENT_SOURCE_KINDS
        or type(source_id) is not int
        or not 0 < source_id <= 2_147_483_647
    ):
        raise ValueError("Некорректный источник документа.")
    value = base64.urlsafe_b64encode(f"{source_kind}:{source_id}".encode()).decode().rstrip("=")
    return f"d1_{value}"


def decode_document_ref(value: str) -> tuple[str, int]:
    """Разрешает только единственное каноническое представление ссылки."""
    if not re.fullmatch(r"d1_[A-Za-z0-9_-]{4,100}", value):
        raise ValueError("Некорректная ссылка документа.")
    try:
        encoded = value[3:]
        decoded = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
        ).decode()
        kind, identifier = decoded.split(":", 1)
        source_id = int(identifier)
        if encode_document_ref(kind, source_id) != value:
            raise ValueError("Неканоническая ссылка документа.")
    except (ValueError, UnicodeError) as error:
        raise ValueError("Некорректная ссылка документа.") from error
    return kind, source_id


def ref_from_source_id(source_id: str) -> str:
    """Превращает проверенный ID каталога знаний в ссылку документа."""
    kind, identifier = source_id.split(":", 1)
    return encode_document_ref(kind, int(identifier))


def document_text_hash(text: str) -> str:
    """Возвращает SHA-256 полного исходного текстового представления UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
