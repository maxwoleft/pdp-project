"""Модель діалогу для зберігання історії та контексту."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str                          # Для user — текст або транскрипт; для assistant — відповідь
    has_image: bool = False
    image_b64: str | None = None          # Тимчасово, для передачі в LLM
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Conversation(BaseModel):
    conversation_id: str                  # f"{channel}:{country}:{external_chat_id}"
    country: str
    channel: str
    # Salon обирається в розмові через list_salons. Поки клієнт не обрав — None.
    # Після обрання — зберігається тут і пробрасується агенту в RUNTIME CONTEXT.
    salon_id: str | None = None
    client_id: str | None = None
    history: list[ConversationTurn] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
