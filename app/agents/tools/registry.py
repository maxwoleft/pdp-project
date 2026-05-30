"""Реєстр Claude tools для конкретної країни.

Кожен handler отримує session_factory і відкриває country-scoped сесію
безпосередньо в момент виконання — це гарантує коротке життя транзакції.
"""
from collections.abc import Awaitable, Callable
from typing import Any

ToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]


class ToolRegistry:
    def __init__(self) -> None:
        self._schemas: list[dict[str, Any]] = []
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, schema: dict[str, Any], handler: ToolHandler) -> None:
        self._schemas.append(schema)
        self._handlers[schema["name"]] = handler

    def schemas(self) -> list[dict[str, Any]]:
        return self._schemas

    async def execute(self, name: str, tool_input: dict[str, Any]) -> Any:
        handler = self._handlers.get(name)
        if not handler:
            return {"error": f"unknown tool: {name}"}
        try:
            return await handler(tool_input)
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
