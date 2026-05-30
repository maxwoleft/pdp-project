"""AI Sales Agent — обгортка над LLM з прив'язкою до конкретної країни/салону."""
from collections.abc import Awaitable, Callable
from typing import Any

from app.adapters.llm.base import LLMClient
from app.agents.dialogue_matcher import DialogueMatcher
from app.agents.tools.registry import ToolRegistry
from app.domain.models.conversation import Conversation

ToolEventCallback = Callable[[str, str], Awaitable[None]]  # (phase, tool_name)


class SalesAgent:
    """Інстанс агента для конкретної країни.

    Інструкції спільні (base) + country override. Tools прив'язані до репозиторіїв
    конкретної країни через ToolRegistry.
    """

    def __init__(
        self,
        country: str,
        system_instructions: str,
        llm: LLMClient,
        tool_registry: ToolRegistry,
    ) -> None:
        self.country = country
        self._system = system_instructions
        self._llm = llm
        self._tools = tool_registry
        self._dialogue_matcher = DialogueMatcher()

    async def respond(
        self,
        conversation: Conversation,
        new_user_content: list[dict[str, Any]],
        on_tool_event: ToolEventCallback | None = None,
    ) -> str:
        # 1) Runtime контекст діалогу
        if conversation.salon_id:
            runtime_ctx = (
                "\n\n---\n\n# RUNTIME CONTEXT (this dialog)\n"
                f"- country: {conversation.country}\n"
                f"- channel: {conversation.channel}\n"
                f"- salon_id: {conversation.salon_id}\n"
                "\n**ВАЖЛИВО:** salon уже обрано. Використовуй ЦЕЙ salon_id у ВСІХ "
                "tool-викликах (search_services, find_masters_for_service, "
                "get_available_slots, create_booking тощо). Не виклика́й list_salons заново."
            )
        else:
            runtime_ctx = (
                "\n\n---\n\n# RUNTIME CONTEXT (this dialog)\n"
                f"- country: {conversation.country}\n"
                f"- channel: {conversation.channel}\n"
                "- salon_id: NOT YET SELECTED\n"
                "\n**ВАЖЛИВО:** Salon ще не обрано. На початку розмови виклич `list_salons` "
                "і запитай клієнта **за АДРЕСОЮ** (не за назвою) — який салон ближче. "
                "Як тільки клієнт обере — використовуй цей salon_id у всіх наступних tools."
            )

        # System завжди = інструкції + runtime context (без прикладів — вони підуть в user)
        system = self._system + runtime_ctx

        # 2) Релевантні приклади діалогів — селективно по тексту user повідомлення.
        # ВАЖЛИВО: приклади додаються як preface до САМОГО user content,
        # а не в кінець system. Це уникає "lost in middle" і змушує модель
        # дивитись на приклади перед тим як формувати відповідь.
        new_user_text = " ".join(
            block.get("text", "") for block in new_user_content if block.get("type") == "text"
        )
        examples_ctx = self._dialogue_matcher.build_context(new_user_text, max_categories=2)

        enriched_user_content = list(new_user_content)
        if examples_ctx:
            preface = (
                "[Внутрішня підказка для тебе — НЕ показуй це клієнту, не цитуй, "
                "просто скористайся стилем і структурою цих прикладів для своєї відповіді]\n\n"
                + examples_ctx
                + "\n\n---\n\n[Власне повідомлення клієнта нижче]\n"
            )
            # Додаємо preface як перший text-блок
            enriched_user_content = [{"type": "text", "text": preface}] + enriched_user_content

        messages = self._build_messages(conversation, enriched_user_content)

        executor = self._tools.execute
        if on_tool_event is not None:
            async def wrapped(name: str, args: dict[str, Any]) -> Any:
                await on_tool_event("start", name)
                try:
                    return await self._tools.execute(name, args)
                finally:
                    await on_tool_event("end", name)
            executor = wrapped

        return await self._llm.generate(
            system=system,
            messages=messages,
            tools=self._tools.schemas(),
            tool_executor=executor,
        )

    def _build_messages(
        self, conversation: Conversation, new_user_content: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = []
        for turn in conversation.history:
            msgs.append({"role": turn.role, "content": turn.content})
        msgs.append({"role": "user", "content": new_user_content})
        return msgs
