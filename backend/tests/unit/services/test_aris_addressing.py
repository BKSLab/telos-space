"""Арис/ARIS вызывается целым именем в начале; прежние имена его не вызывают."""

from src.agent.aris import ARIS_NAME, ArisMode, is_addressed_to_aris, strip_address
from src.prompts.agent_routing import AGENT_ROUTING_SYSTEM_PROMPT
from src.prompts.project_agent import ARIS_BASE_PROMPTS, ARIS_SYSTEM_PROMPTS


def test_message_is_for_aris_only_when_it_starts_with_the_whole_word():
    addressed = [
        "Арис, что горит?",
        "АРИС перенеси срок",
        "арис",
        "  Арис: сводка по проекту  ",
        "арис!",
        "Арис\nкакие задачи?",
        "АрИс — сводка",
        "Арис, PM-план готов?",
        "ARIS, что горит?",
        "aris перенеси срок",
        "ArIs",
        "\tARIS; сводка",
        "ARIS?",
        "ARIS\nкакие задачи?",
        "ARIS–сводка",
        "Арис… сводка",
        "ARIS(сводка)",
    ]
    not_addressed = [
        "Аристотель, привет",
        "Ариса спросили",
        "Арис2 это логин",
        "ARIS2 это логин",
        "Aristo отчёт",
        "АрисO отчёт",
        "ARISА отчёт",
        "Арис_бот, привет",
        "ARIS_bot, привет",
        "коллеги, Арис сказал",
        "@Арис привет",
        "@ARIS привет",
        "",
        None,
        "Вера, сводка",
        "ВЕРА перенеси срок",
        "вера",
        "Тим, сводка",
        "ТИМ перенеси срок",
        "тим",
    ]
    wrong = [text for text in addressed if not is_addressed_to_aris(text)]
    wrong += [text for text in not_addressed if is_addressed_to_aris(text)]
    assert not wrong, f"Адресация распознана неверно: {wrong}"


def test_strip_address_keeps_the_question_itself():
    cases = {
        "Арис, какие задачи в проекте?": "какие задачи в проекте?",
        "АРИС перенеси срок": "перенеси срок",
        "арис: — сводка": "сводка",
        "Арис": "Арис",
        "  ARIS, \t\n": "ARIS,",
        "ARIS": "ARIS",
        "aRiS, какие задачи?": "какие задачи?",
        "\tARIS;!? —– сводка  ": "сводка",
        "ARIS\nзадачи": "задачи",
        "ARIS(сводка)": "(сводка)",
        "Аристотель, привет": "Аристотель, привет",
        "Арис2, привет": "Арис2, привет",
        "ARIS2, привет": "ARIS2, привет",
        "Арис, PM-план готов?": "PM-план готов?",
        "Арис PMO отчёт": "PMO отчёт",
        "ARIS PM: сводка": "PM: сводка",
        "Вера, сводка": "Вера, сводка",
        "Тим, сводка": "Тим, сводка",
        "коллеги, Арис, сводка": "коллеги, Арис, сводка",
    }
    assert {text: strip_address(text) for text in cases} == cases


def test_agent_identity_is_aris_project_manager_of_telos_space_in_every_mode():
    assert ARIS_NAME == "Арис"
    for prompts in (ARIS_SYSTEM_PROMPTS, ARIS_BASE_PROMPTS):
        for mode in ArisMode:
            prompt = prompts[mode]
            assert "Тебя зовут Арис (ARIS)" in prompt and "проектный менеджер TELOS SPACE" in prompt
            assert "мужском роде" in prompt
            assert "цель → контекст → действие → результат" in prompt
            assert "Прежние имена в истории" in prompt
            assert "Вера" not in prompt and "Тим" not in prompt
    assert "Арис (ARIS), проектный менеджер TELOS SPACE" in AGENT_ROUTING_SYSTEM_PROMPT
    assert "мужском роде" in AGENT_ROUTING_SYSTEM_PROMPT
