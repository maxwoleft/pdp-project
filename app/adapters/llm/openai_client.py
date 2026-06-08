"""OpenAI Chat Completions клієнт з підтримкою tool use.

Реалізує той самий LLMClient інтерфейс, що й AnthropicClient — щоб router
міг прозоро підмінювати реалізацію.

Anthropic vs OpenAI tools — формати різні, конвертуємо тут.
"""
from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

from openai import AsyncOpenAI

from app.adapters.llm.base import LLMClient
from app.core.config import get_settings


def _anthropic_tools_to_openai(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Конвертує наш Anthropic-формат у OpenAI."""
    out: list[dict[str, Any]] = []
    for t in tools or []:
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return out


def _anthropic_messages_to_openai(
    system: str, messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Конвертує Anthropic-стиль messages у OpenAI Chat Completions.

    Anthropic: content може бути string або list of blocks (text, image, tool_use, tool_result).
    OpenAI: інші типи блоків + окремий tool_calls/tool messages.
    """
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]

    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        # content — список блоків
        if role == "user":
            # tool_result блоки → окремі повідомлення з role=tool
            text_parts: list[dict[str, Any]] = []
            tool_results: list[dict[str, Any]] = []
            for block in content:
                btype = block.get("type")
                if btype == "text":
                    text_parts.append({"type": "text", "text": block["text"]})
                elif btype == "image":
                    src = block.get("source", {})
                    data = src.get("data", "")
                    media = src.get("media_type", "image/jpeg")
                    text_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{media};base64,{data}"},
                    })
                elif btype == "tool_result":
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": block["tool_use_id"],
                        "content": str(block.get("content", "")),
                    })
            if text_parts:
                # OpenAI multimodal: якщо лише текст без зображень — використовуй простий string
                if len(text_parts) == 1 and text_parts[0]["type"] == "text":
                    out.append({"role": "user", "content": text_parts[0]["text"]})
                else:
                    out.append({"role": "user", "content": text_parts})
            out.extend(tool_results)

        elif role == "assistant":
            text_chunks: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            for block in content:
                btype = block.get("type")
                if btype == "text":
                    text_chunks.append(block["text"])
                elif btype == "tool_use":
                    tool_calls.append({
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block.get("input", {})),
                        },
                    })
            assistant_msg: dict[str, Any] = {"role": "assistant"}
            if text_chunks:
                assistant_msg["content"] = "\n".join(text_chunks)
            else:
                assistant_msg["content"] = None
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            out.append(assistant_msg)
    return out


class OpenAIClient(LLMClient):
    def __init__(self, model: str | None = None) -> None:
        settings = get_settings()
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = model or settings.openai_chat_model
        self._max_tokens = settings.openai_max_tokens

    async def generate(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_executor: Any = None,
    ) -> str:
        oai_messages = _anthropic_messages_to_openai(system, messages)
        oai_tools = _anthropic_tools_to_openai(tools or [])

        while True:
            create_kwargs: dict[str, Any] = {
                "model": self._model,
                "messages": oai_messages,
                "tools": oai_tools or None,
                "max_completion_tokens": self._max_tokens,
            }
            # Дозволяємо LLM кликати декілька tools одночасно (find_masters + get_slots) —
            # значно зменшує end-to-end latency для booking flow.
            if oai_tools:
                create_kwargs["parallel_tool_calls"] = True
            resp = await self._client.chat.completions.create(**create_kwargs)
            choice = resp.choices[0]
            msg = choice.message

            # Token usage + prompt cache statistics
            try:
                usage = getattr(resp, "usage", None)
                if usage:
                    prompt_t = getattr(usage, "prompt_tokens", 0)
                    comp_t = getattr(usage, "completion_tokens", 0)
                    details = getattr(usage, "prompt_tokens_details", None)
                    cached_t = getattr(details, "cached_tokens", 0) if details else 0
                    cache_pct = round(100 * cached_t / prompt_t, 1) if prompt_t else 0
                    import logging
                    logging.getLogger("openai_usage").info(
                        "[USAGE] prompt=%d completion=%d cached=%d (%.1f%%)",
                        prompt_t, comp_t, cached_t, cache_pct,
                    )
            except Exception:
                pass

            if msg.tool_calls:
                # Додаємо assistant з tool_calls + результати
                oai_messages.append({
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in msg.tool_calls
                    ],
                })
                # Виконуємо ВСІ tool calls паралельно (asyncio.gather) —
                # find_masters + get_slots одночасно швидше у ~2x.
                parsed: list[tuple[Any, dict[str, Any]]] = []
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    parsed.append((tc, args))

                results = await asyncio.gather(*[
                    tool_executor(tc.function.name, args) for tc, args in parsed
                ], return_exceptions=True)

                for (tc, _), result in zip(parsed, results):
                    if isinstance(result, BaseException):
                        result = {"error": str(result)}
                    oai_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": str(result),
                    })
                continue

            return (msg.content or "").strip()
