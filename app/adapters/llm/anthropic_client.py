"""Anthropic Claude клієнт з підтримкою tool use (agentic loop)."""
from typing import Any

from anthropic import AsyncAnthropic

from app.adapters.llm.base import LLMClient
from app.core.config import get_settings


class AnthropicClient(LLMClient):
    def __init__(self) -> None:
        settings = get_settings()
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_model
        self._max_tokens = settings.anthropic_max_tokens

    async def generate(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_executor: Any = None,
    ) -> str:
        """Agentic loop з prompt caching.

        System prompt позначається cache_control=ephemeral — Claude кешує його
        на ~5 хв і input-токени кешованої частини коштують в 10× дешевше.
        """
        current_messages = list(messages)
        # System як список з cache marker — потрібно для prompt caching
        system_blocks = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]

        while True:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system_blocks,
                messages=current_messages,
                tools=tools or [],
            )

            if response.stop_reason == "tool_use":
                # Обробляємо всі tool_use блоки в одній ітерації
                assistant_content = []
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        assistant_content.append(block.model_dump())
                        result = await tool_executor(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(result),
                        })
                    elif block.type == "text":
                        assistant_content.append({"type": "text", "text": block.text})

                current_messages.append({"role": "assistant", "content": assistant_content})
                current_messages.append({"role": "user", "content": tool_results})
                continue

            # Фінальна відповідь
            text_parts = [b.text for b in response.content if b.type == "text"]
            return "\n".join(text_parts).strip()
