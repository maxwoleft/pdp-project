"""Hybrid LLM роутер: вибирає GPT-5 mini або Claude Sonnet залежно від запиту.

Стратегія:
- Прості діалоги (привітання, FAQ, "де знаходитесь", "коли працюєте") → GPT-5 mini.
  Дешевше у ~10 разів, для FAQ якості достатньо.
- Складні діалоги, де критична tool-use дисципліна (booking, cancel, ціни,
  пошук послуг) → Claude Sonnet 4.6. Він краще тримає інструкції і не
  вигадує даних.
- Якщо у поточному діалозі вже почався booking flow (бачимо tool_use в історії)
  — лишаємось на Claude до завершення діалогу. Уникає змішування стилів.
"""
from __future__ import annotations

from typing import Any

from app.adapters.llm.base import LLMClient
from app.adapters.llm.openai_client import OpenAIClient
from app.core.config import get_settings

# Ключові слова → Claude Sonnet 4.6 (складні task'и)
COMPLEX_KEYWORDS = (
    # Booking
    "запис", "записа", "записать", "записаться", "book", "appointment",
    # Cancel / reschedule
    "скасув", "відмін", "отмен", "cancel", "перенест", "перенос", "reschedule",
    # Service identification
    "манікюр", "маникюр", "manicure", "педикюр", "pedicure",
    "стрижк", "стрижку", "haircut", "укладк",
    "фарб", "красит", "color", "тонуван", "тонир", "корен",
    "ботокс", "botox", "кератин", "keratin",
    "брів", "бров", "brow", "вії", "ресн", "lash",
    "масаж", "массаж", "massage", "robolex", "icoone",
    # Prices
    "ціна", "цена", "вартість", "стоимость", "коштує", "стоит", "price",
    # Time / availability
    "вільн", "свобод", "вільн", "коли", "когда", "what time",
    # Master / employee
    "майстер", "майстр", "мастер", "master",
    # Slots
    "час", "годин", "час", "time", "slot", "вікно",
)


def _is_complex(text: str, history: list[dict[str, Any]]) -> bool:
    if not text:
        text = ""
    text_lower = text.lower()

    # 1. Якщо у поточному діалозі вже були tool_use — залишаємось на Claude (sticky)
    for msg in history:
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") in ("tool_use", "tool_result"):
                    return True

    # 2. Keyword match на новому повідомленні
    for kw in COMPLEX_KEYWORDS:
        if kw in text_lower:
            return True

    return False


class HybridLLMRouter(LLMClient):
    def __init__(
        self,
        primary: LLMClient | None = None,
        fallback: LLMClient | None = None,
    ) -> None:
        settings = get_settings()
        # primary = "дорога", якісна модель для складних кейсів (GPT-5)
        # fallback = "дешева" модель для простих (GPT-5 mini)
        self._primary = primary or OpenAIClient(model=settings.openai_chat_model_complex)
        self._fallback = fallback or OpenAIClient(model=settings.openai_chat_model)

    async def generate(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_executor: Any = None,
    ) -> str:
        # Беремо текст останнього user-повідомлення для класифікації
        last_user_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content")
                if isinstance(content, str):
                    last_user_text = content
                elif isinstance(content, list):
                    parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                    last_user_text = " ".join(parts)
                break

        history_for_check = messages[:-1] if messages else []
        complex_request = _is_complex(last_user_text, history_for_check)

        client = self._primary if complex_request else self._fallback
        return await client.generate(system, messages, tools=tools, tool_executor=tool_executor)
