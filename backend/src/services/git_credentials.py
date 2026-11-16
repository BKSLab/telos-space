"""Шифрование credentials Git отдельным ключом; секреты не являются DTO."""

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

from src.exceptions.git_sources import GitCredentialUnavailableError


class GitCredentialCodec:
    """Шифрует ограниченный GitHub credential независимо от JWT/session secret."""

    def __init__(self, key: SecretStr | None) -> None:
        self._fernet: Fernet | None = None
        if key is not None and key.get_secret_value():
            try:
                self._fernet = Fernet(key.get_secret_value().encode("ascii"))
            except ValueError:
                pass
            if self._fernet is None:
                raise GitCredentialUnavailableError()

    @property
    def enabled(self) -> bool:
        """Доступна ли запись/расшифровка credentials в этой установке."""
        return self._fernet is not None

    def encrypt(self, value: SecretStr) -> str:
        """Возвращает ciphertext; при пустом ключе отказывает без fallback."""
        if self._fernet is None:
            raise GitCredentialUnavailableError()
        return self._fernet.encrypt(value.get_secret_value().encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> SecretStr:
        """Расшифровывает credential, не включая ciphertext в ошибку."""
        if self._fernet is None:
            raise GitCredentialUnavailableError()
        try:
            return SecretStr(self._fernet.decrypt(value.encode("ascii")).decode("utf-8"))
        except (InvalidToken, ValueError):
            pass
        raise GitCredentialUnavailableError()
