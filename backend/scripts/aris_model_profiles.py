"""Согласованные конфигурации сравнения Ариса, без секретов и изменения .env."""

from dataclasses import dataclass

from src.core.settings import LlmSettings


@dataclass(frozen=True)
class ModelProfile:
    name: str
    model: str | None = None
    provider: str = ""
    reasoning_enabled: bool | None = None
    reasoning_effort: str | None = None

    def apply(self, settings: LlmSettings) -> LlmSettings:
        """Возвращает параметры кандидата; baseline сохраняет текущую конфигурацию."""
        if self.model is None:
            return settings.model_copy()
        return settings.model_copy(
            update={
                "agent_model": self.model,
                "agent_provider": self.provider,
                "agent_reasoning_enabled": self.reasoning_enabled,
                "agent_reasoning_effort": self.reasoning_effort,
            }
        )


PROFILES = (
    # Имя сохранено для прежних артефактов; контроль не зависит от активной модели.
    ModelProfile("gemini-current", "google/gemini-3.7-flash"),
    ModelProfile("qwen38-thinking", "qwen/qwen3.8-flash", "alibaba", True),
    ModelProfile("qwen38-direct", "qwen/qwen3.8-flash", "alibaba", False),
    ModelProfile("qwen35-direct", "qwen/qwen3.5-flash-02-23", "alibaba", False),
    ModelProfile("luna-standard", "openai/gpt-6-luna", "openai", False, "none"),
)
