"""FastAPI Depends getter'и для основних компонентів."""
from fastapi import Request

from app.adapters.messengers.base import MessengerAdapter
from app.domain.services.conversation_service import ConversationService
from app.domain.services.message_batcher import MessageBatcher


def get_conversation_service(request: Request) -> ConversationService:
    return request.app.state.conversation_service


def get_message_batcher(request: Request) -> MessageBatcher:
    return request.app.state.message_batcher


def get_messenger(channel: str):
    """Фабрика залежності — повертає Depends-callable для конкретного каналу."""
    def _dep(request: Request) -> MessengerAdapter:
        registry = request.app.state.messenger_registry
        if channel not in registry:
            raise RuntimeError(f"Messenger '{channel}' not registered")
        return registry[channel]
    return _dep
