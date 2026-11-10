"""Арис — проектный менеджер и правила обращения в проектном чате."""

import re
from enum import StrEnum

ARIS_NAME = "Арис"
"""Имя агента для людей и модели; допустимые обращения — «Арис» и «ARIS»."""
_ADDRESS = re.compile(r"(?:арис|aris)(?!\w)", re.IGNORECASE)
# Разделители после обращения, которые не несут смысла вопроса: «Арис, …», «ARIS: …».
_ADDRESS_TAIL = " \t\r\n,.:;!?-—–"


class ArisMode(StrEnum):
    """Где Арис отвечает: от этого зависят данные, инструменты и секция промпта."""

    PROJECT_CHAT = "project_chat"
    """Общий чат проекта: ответ видит вся команда, только данные этого проекта."""
    WIDGET_PROJECT = "widget_project"
    """Личный виджет, страница указывает на проект: его данные плюс личные действия."""
    WIDGET_PERSONAL = "widget_personal"
    """Личный виджет без проекта на странице: мои задачи, разовые, поиск."""


ALL_MODES = frozenset(ArisMode)
PROJECT_MODES = frozenset({ArisMode.PROJECT_CHAT, ArisMode.WIDGET_PROJECT})
WIDGET_MODES = frozenset({ArisMode.WIDGET_PROJECT, ArisMode.WIDGET_PERSONAL})


def is_addressed_to_aris(text: str | None) -> bool:
    """Адресовано ли сообщение чата Арису.

    Args:
        text: Текст сообщения как его отправил участник.

    Returns:
        True для «Арис, что горит?», «ARIS перенеси срок», «арис»; False для
        «Аристотель», «Арис2», «коллеги, Арис сказал» и прежних имён.
    """
    return _ADDRESS.match((text or "").strip()) is not None


def strip_address(text: str) -> str:
    """Убирает обращение «Арис» или «ARIS» и разделители из начала вопроса.

    Пустой остаток — исходный текст.
    """
    stripped = text.strip()
    address = _ADDRESS.match(stripped)
    if address is None:
        return stripped
    rest = stripped[address.end() :]
    rest = rest.lstrip(_ADDRESS_TAIL).strip()
    return rest or stripped
