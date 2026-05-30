"""Контракт для LLM-клієнта (щоб можна було підмінити Anthropic на інший провайдер у тестах)."""
from abc import ABC, abstractmethod
from typing import Any


class LLMClient(ABC):
    @abstractmethod
    async def generate(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_executor: Any = None,
    ) -> str:
        """Повертає фінальну текстову відповідь після (можливих) tool-use ітерацій."""
        ...
