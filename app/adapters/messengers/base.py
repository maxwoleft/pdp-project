"""Базовий контракт для месенджер-адаптерів.

Адаптер — STATELESS. Один інстанс на канал, обслуговує всі salon-аккаунти цього каналу.
Облікові дані передаються у кожен виклик через `credentials` (з salon_messenger).
"""
from abc import ABC, abstractmethod
from typing import Any

from app.domain.models.message import IncomingMessage, OutgoingMessage


class MessengerAdapter(ABC):
    """Кожен месенджер реалізує цей інтерфейс."""

    channel_name: str

    @abstractmethod
    async def parse_webhook(
        self,
        payload: dict[str, Any],
        country: str,
        salon_id: str | None,
        credentials: dict[str, Any],
    ) -> list[IncomingMessage]:
        ...

    @abstractmethod
    async def download_media(self, media_url: str, credentials: dict[str, Any]) -> bytes:
        ...

    @abstractmethod
    async def send_text(self, message: OutgoingMessage, credentials: dict[str, Any]) -> None:
        ...
