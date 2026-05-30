"""Дебаунсер вхідних повідомлень.

Клієнт може писати кілька повідомлень підряд. Замість того щоб обробляти
кожне окремо (і отримувати кілька відповідей агента), ми накопичуємо їх
у буфері. Кожне нове повідомлення скидає таймер. Після тиші DEBOUNCE_SECONDS
батч відправляється в обробник одним викликом.

Ключ батчу = (channel, country, external_chat_id) — окремий буфер на кожен діалог.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.domain.models.message import IncomingMessage

log = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 8.0

BatchHandler = Callable[[list[IncomingMessage], dict[str, Any]], Awaitable[None]]


@dataclass
class _Buffer:
    messages: list[IncomingMessage] = field(default_factory=list)
    credentials: dict[str, Any] = field(default_factory=dict)
    task: asyncio.Task | None = None


class MessageBatcher:
    def __init__(
        self,
        handler: BatchHandler,
        debounce_seconds: float = DEBOUNCE_SECONDS,
    ) -> None:
        self._handler = handler
        self._debounce = debounce_seconds
        self._buffers: dict[tuple, _Buffer] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(msg: IncomingMessage) -> tuple:
        return (msg.channel.value, msg.country, msg.external_chat_id)

    async def add(self, msg: IncomingMessage, credentials: dict[str, Any]) -> None:
        """Додає повідомлення в буфер і (пере)запускає таймер."""
        key = self._key(msg)
        async with self._lock:
            buf = self._buffers.get(key)
            if buf is None:
                buf = _Buffer()
                self._buffers[key] = buf

            buf.messages.append(msg)
            buf.credentials = credentials  # завжди беремо найновіші

            if buf.task and not buf.task.done():
                buf.task.cancel()

            buf.task = asyncio.create_task(self._flush_after_delay(key))

    async def _flush_after_delay(self, key: tuple) -> None:
        try:
            await asyncio.sleep(self._debounce)
        except asyncio.CancelledError:
            return  # таймер скинуто новим повідомленням

        async with self._lock:
            buf = self._buffers.pop(key, None)

        if not buf or not buf.messages:
            return

        try:
            await self._handler(buf.messages, buf.credentials)
        except Exception:  # noqa: BLE001
            log.exception("Batch handler failed for %s", key)

    async def shutdown(self) -> None:
        """Скасовує всі активні таймери (для cleanup на завершенні)."""
        async with self._lock:
            for buf in self._buffers.values():
                if buf.task and not buf.task.done():
                    buf.task.cancel()
            self._buffers.clear()
