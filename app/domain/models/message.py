"""Канонічні моделі повідомлень — внутрішній формат, незалежний від месенджера."""
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"


class Channel(str, Enum):
    TELEGRAM = "telegram"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    VIBER = "viber"
    WHATSAPP = "whatsapp"
    WEB = "web"


class IncomingMessage(BaseModel):
    """Уніфіковане вхідне повідомлення після парсингу webhook."""
    channel: Channel
    country: str                          # ISO код, напр. "ua"
    salon_id: str | None = None           # None для country-level месенджерів — обирається в розмові
    external_user_id: str                 # ID юзера в месенджері
    external_chat_id: str
    message_type: MessageType
    text: str | None = None               # Для TEXT, або caption для IMAGE
    media_url: str | None = None          # Для IMAGE/AUDIO
    media_mime: str | None = None
    received_at: datetime = Field(default_factory=datetime.utcnow)
    raw: dict = Field(default_factory=dict)  # Оригінальний payload (для аудиту)


class OutgoingMessage(BaseModel):
    """Відповідь, яку відправляємо клієнту. Завжди текст (за вимогами)."""
    channel: Channel
    external_chat_id: str
    text: str
