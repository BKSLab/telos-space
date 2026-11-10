"""Выпуск и распознавание API-ключей с выбранными правами.

Формат ключа: ``telos_key_<prefix>_<secret>``:

- ``prefix`` — 8 случайных hex-символов, по ним ключ узнают в списке;
- ``secret`` — 32 случайных hex-символа (128 бит).

В базе хранится SHA-256 всего ключа. Медленная функция не нужна: энтропии
ключа хватает, чтобы перебор был бессмысленным, а проверка идёт на каждый
запрос агента.

READ/WRITE-токены имеют отдельный формат ``telos_token_<secret>``.
В поле key_prefix хранится только публичный восьмизначный идентификатор.
"""

import hashlib
import hmac
import secrets

API_KEY_PREFIX = "telos_key_"
_PREFIX_HEX = 8
_SECRET_HEX = 32


def generate_api_key() -> tuple[str, str]:
    """Выпускает новый ключ.

    Returns:
        Пара ``(raw_key, key_prefix)``: полный ключ показывается один раз,
        префикс хранится для узнавания в списке.
    """
    prefix_part = secrets.token_hex(_PREFIX_HEX // 2)
    secret_part = secrets.token_hex(_SECRET_HEX // 2)
    return (
        f"{API_KEY_PREFIX}{prefix_part}_{secret_part}",
        prefix_part,
    )


def hash_api_key(raw_key: str) -> str:
    """Возвращает SHA-256 ключа в шестнадцатеричном виде."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def verify_api_key(raw_key: str, key_hash: str) -> bool:
    """Сверяет ключ с сохранённым хешем за постоянное время."""
    return hmac.compare_digest(hash_api_key(raw_key), key_hash)


def is_api_key(secret: str | None) -> bool:
    """Определяет тип предъявленного секрета по каноническому маркеру."""
    return bool(secret) and secret.startswith(API_KEY_PREFIX)


def key_prefix_of(raw_key: str) -> str | None:
    """Достаёт публичный префикс из предъявленного ключа.

    Returns:
        Публичные восемь hex-символов либо ``None`` при неверном формате.
    """
    if not raw_key.startswith(API_KEY_PREFIX):
        return None
    parts = raw_key[len(API_KEY_PREFIX):].split("_")
    if (
        len(parts) != 2
        or len(parts[0]) != _PREFIX_HEX
        or len(parts[1]) != _SECRET_HEX
        or any(character not in "0123456789abcdef" for part in parts for character in part)
    ):
        return None
    return parts[0]
