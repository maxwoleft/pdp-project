"""Зберігання історії діалогу в Redis (короткострокова пам'ять)."""
import json

from redis.asyncio import Redis

from app.domain.models.conversation import Conversation

# 14 днів — щоб поточний контекст спілкування зберігався достатньо довго.
# Більш стара історія (попередні візити) береться з БД через get_client_bookings.
CONV_TTL_SECONDS = 60 * 60 * 24 * 14


class ConversationCache:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def load(self, conversation_id: str) -> Conversation | None:
        raw = await self._redis.get(self._key(conversation_id))
        if not raw:
            return None
        return Conversation.model_validate_json(raw)

    async def save(self, conversation: Conversation) -> None:
        await self._redis.set(
            self._key(conversation.conversation_id),
            conversation.model_dump_json(),
            ex=CONV_TTL_SECONDS,
        )

    @staticmethod
    def _key(conversation_id: str) -> str:
        return f"conv:{conversation_id}"
