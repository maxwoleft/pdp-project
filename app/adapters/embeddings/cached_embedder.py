"""Кешуючий wrapper над embedder'ом. Зберігає embeddings у Redis за хешем тексту.

Embeddings стабільні (text-embedding-3-small детермінований), тому довгий TTL — OK.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from redis.asyncio import Redis

from app.adapters.embeddings.openai_embedder import EMBEDDING_DIM, EMBEDDING_MODEL, OpenAIEmbedder

log = logging.getLogger(__name__)

CACHE_PREFIX = "emb:"
CACHE_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 днів


def _key(text: str) -> str:
    h = hashlib.sha256(f"{EMBEDDING_MODEL}|{text}".encode("utf-8")).hexdigest()
    return f"{CACHE_PREFIX}{h}"


class CachedEmbedder:
    """Прозорий wrapper. Той самий інтерфейс що й OpenAIEmbedder."""

    def __init__(self, inner: OpenAIEmbedder, redis: Redis) -> None:
        self._inner = inner
        self._redis = redis

    async def embed(self, text: str) -> list[float]:
        text = (text or "").strip()
        if not text:
            return [0.0] * EMBEDDING_DIM
        k = _key(text)
        try:
            raw = await self._redis.get(k)
            if raw:
                return json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            log.warning("emb cache get failed: %s", exc)
        vec = await self._inner.embed(text)
        try:
            await self._redis.set(k, json.dumps(vec), ex=CACHE_TTL_SECONDS)
        except Exception as exc:  # noqa: BLE001
            log.warning("emb cache set failed: %s", exc)
        return vec

    async def embed_batch(self, texts: list[str], normalize_names: bool = False) -> list[list[float]]:
        # batch для індексації — без кешу, бо викликається з cron'у де unique тексти
        return await self._inner.embed_batch(texts, normalize_names=normalize_names)

    def __getattr__(self, name: str) -> Any:
        # Прозоро прокидаємо інші атрибути (normalize_service_name, dim тощо)
        return getattr(self._inner, name)
