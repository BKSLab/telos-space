"""Эффект канонического действия определяется сервером, не аргументами модели."""

from typing import Literal

EXTERNAL_RELEASE_ACTIONS = frozenset({"telos_request_web_search", "telos_request_web_page"})


def action_effect(name: str) -> Literal["write", "external_release"]:
    return "external_release" if name in EXTERNAL_RELEASE_ACTIONS else "write"
