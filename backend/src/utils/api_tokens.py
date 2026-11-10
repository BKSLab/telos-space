"""Выпуск и проверка секретов токенов внешних клиентов."""

import hashlib
import secrets

TOKEN_PREFIX = "telos_token_"
TOKEN_ENTROPY_BYTES = 32
DISPLAY_PREFIX_LENGTH = 8
BEARER_SCHEME = "bearer"


def generate_token_secret() -> str:
    """Выпускает новый секрет токена.

    Returns:
        Секрет вида ``telos_token_<случайная часть>``; показывается один раз.
    """
    return f"{TOKEN_PREFIX}{secrets.token_urlsafe(TOKEN_ENTROPY_BYTES)}"


def hash_token_secret(secret: str) -> str:
    """Считает хеш секрета для хранения и поиска.

    Секрет высокоэнтропийный, поэтому медленная функция здесь не нужна:
    перебор невозможен, а проверка выполняется на каждый запрос агента.

    Args:
        secret: Предъявленный секрет токена.

    Returns:
        SHA-256 в шестнадцатеричном виде.
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def build_display_prefix(secret: str) -> str:
    """Возвращает часть секрета, по которой владелец узнаёт токен в списке.

    Args:
        secret: Секрет токена.

    Returns:
        Первые символы секрета без случайной части.
    """
    if not secret.startswith(TOKEN_PREFIX):
        raise ValueError("Неверный формат токена TELOS SPACE.")
    return secret[len(TOKEN_PREFIX):][:DISPLAY_PREFIX_LENGTH]


def extract_bearer_secret(authorization: str | None) -> str | None:
    """Достаёт секрет из заголовка ``Authorization: Bearer <token>``.

    Разбор заголовка одинаков для HTTP и MCP, поэтому живёт в одном месте:
    две реализации схемы предъявления однажды разойдутся по краевым случаям.

    Args:
        authorization: Значение заголовка ``Authorization`` или ``None``.

    Returns:
        Секрет токена либо ``None``, если предъявлена другая схема.
    """
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.strip().lower() != BEARER_SCHEME:
        return None
    secret = value.strip()
    return secret or None
