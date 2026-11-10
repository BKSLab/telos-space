"""Проверки выпуска и хеширования секретов токенов доступа."""

from src.utils.api_tokens import (
    DISPLAY_PREFIX_LENGTH,
    TOKEN_PREFIX,
    build_display_prefix,
    generate_token_secret,
    hash_token_secret,
)


def test_secret_is_unique_hashed_and_never_stored_in_clear() -> None:
    """Секрет имеет префикс и энтропию, каждый новый уникален, хеш устойчив, различается и не содержит секрета, отображаемый префикс короткий."""
    # Секрет узнаваем по префиксу и достаточно длинный.
    secret = generate_token_secret()

    assert secret.startswith(TOKEN_PREFIX)
    assert len(secret) > len(TOKEN_PREFIX) + 30
    # Два выпуска подряд не дают одинаковый секрет.
    secrets_set = {generate_token_secret() for _ in range(50)}

    assert len(secrets_set) == 50
    # Хеш детерминирован для одного секрета и различается для разных.
    first = generate_token_secret()
    second = generate_token_secret()

    assert hash_token_secret(first) == hash_token_secret(first)
    assert hash_token_secret(first) != hash_token_secret(second)
    assert len(hash_token_secret(first)) == 64
    # В хеше не остаётся исходного секрета.
    secret = generate_token_secret()

    assert secret not in hash_token_secret(secret)
    # Префикс для списка короткий и совпадает с началом секрета.
    secret = generate_token_secret()
    prefix = build_display_prefix(secret)

    assert len(prefix) == DISPLAY_PREFIX_LENGTH
    assert secret[len(TOKEN_PREFIX):].startswith(prefix)
    assert not prefix.startswith("telos_")
