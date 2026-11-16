"""Ограниченные по назначению подписи разрешённых публичных тем и страниц."""

import base64
import binascii
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal


class WebGrantSigner:
    """Подписывает точный пользовательский ввод, без сетевых или DB-эффектов."""

    def __init__(
        self,
        *,
        secret: str,
        ttl_seconds: int = 900,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not secret or not 60 <= ttl_seconds <= 900:
            raise ValueError("Нужны секрет подписи и TTL от 60 до 900 секунд.")
        self._key = hmac.digest(secret.encode(), b"telos.web-research.grants.v1", "sha256")
        self.ttl_seconds = ttl_seconds
        self.clock = clock

    def issue(
        self,
        *,
        project_id: int,
        user_id: int,
        kind: Literal["topic", "page"],
        value: str,
        approval_id: str,
        expires_at: datetime | None = None,
    ) -> tuple[str, datetime]:
        """Срок производной страницы не продлевает исходное разрешение."""
        deadline = min(
            expires_at or self.clock() + timedelta(seconds=self.ttl_seconds),
            self.clock() + timedelta(seconds=self.ttl_seconds),
        )
        payload = json.dumps(
            {
                "v": 1,
                "p": project_id,
                "u": user_id,
                "k": kind,
                "q": value,
                "a": approval_id,
                "exp": int(deadline.timestamp()),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        body = base64.urlsafe_b64encode(payload).rstrip(b"=")
        signature = base64.urlsafe_b64encode(hmac.digest(self._key, body, "sha256")).rstrip(b"=")
        return (body + b"." + signature).decode(), deadline

    def verify(
        self, token: str, *, project_id: int, user_id: int, kind: Literal["topic", "page"]
    ) -> dict:
        """Не принимает чужой проект/actor, другое назначение и просроченную подпись."""
        try:
            if len(token) > 8192:
                raise ValueError
            body, signature = token.encode("ascii").split(b".")
            expected = base64.urlsafe_b64encode(hmac.digest(self._key, body, "sha256")).rstrip(b"=")
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            payload = json.loads(
                base64.b64decode(body + b"=" * (-len(body) % 4), altchars=b"-_", validate=True)
            )
            if (
                not isinstance(payload, dict)
                or payload.get("v") != 1
                or payload.get("p") != project_id
                or payload.get("u") != user_id
                or payload.get("k") != kind
                or not isinstance(payload.get("q"), str)
                or not isinstance(payload.get("a"), str)
                or not isinstance(payload.get("exp"), int)
                or payload["exp"] <= self.clock().timestamp()
            ):
                raise ValueError
            return payload
        except (ValueError, UnicodeError, TypeError, binascii.Error, RecursionError):
            raise ValueError("Разрешение внешнего исследования недействительно.") from None
