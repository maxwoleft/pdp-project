"""OpenAI embeddings адаптер.

Модель: text-embedding-3-small (1536 dim, $0.02/1M токенів).

Підтримує:
- одиничний embed (для запитів клієнтів)
- batch embed (для індексування послуг — до 2048 текстів за раз)
- normalize_service_name: чистить мультимовні префікси, щоб embedding був гострішим
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

from openai import AsyncOpenAI

from app.core.config import get_settings

log = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536
BATCH_SIZE = 100  # OpenAI приймає до 2048, але 100 — безпечно з точки зору latency / помилок

# Чистимо мовні мітки які CRM додає у назви послуг ("EN Root... / UA Фарб... / RUS Окрас...").
# Без цього embedding розмазується між трьома мовами і втрачає чіткість.
_LANG_TAG = re.compile(r"\b(EN|UA|RUS?|PL)\s+", re.IGNORECASE)
_MULTI_SLASH = re.compile(r"\s*/\s*")
_WS = re.compile(r"\s+")


def normalize_service_name(name: str) -> str:
    """Перетворює 'EN Root colouring / UA Фарбування кореня / RUS Окрашивание корней'
    на 'Root colouring | Фарбування кореня | Окрашивание корней'.

    Це дає embedding-моделі чистіший контент: вона бачить три варіанти на трьох мовах
    без шуму від тегів і отримує гостріший семантичний вектор.
    """
    if not name:
        return ""
    # Викидаємо мовні теги (EN, UA, RUS, PL)
    cleaned = _LANG_TAG.sub("", name)
    # Замінюємо ' / ' на ' | ' — для відділення мовних варіантів
    cleaned = _MULTI_SLASH.sub(" | ", cleaned)
    # Прибираємо подвійні пробіли
    cleaned = _WS.sub(" ", cleaned).strip()
    return cleaned


class OpenAIEmbedder:
    def __init__(self) -> None:
        settings = get_settings()
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = EMBEDDING_MODEL

    async def embed(self, text: str) -> list[float]:
        """Embed одного рядка. Використовується для клієнтських пошукових запитів."""
        text = (text or "").strip()
        if not text:
            return [0.0] * EMBEDDING_DIM
        resp = await self._client.embeddings.create(model=self._model, input=text)
        return resp.data[0].embedding

    async def embed_batch(self, texts: list[str], normalize_names: bool = False) -> list[list[float]]:
        """Batch embed для індексування. Розбиває на чанки по BATCH_SIZE.

        normalize_names=True — застосувати normalize_service_name до кожного входу
        (для embed-у назв послуг з CRM).
        """
        if not texts:
            return []
        if normalize_names:
            texts = [normalize_service_name(t) for t in texts]
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH_SIZE):
            chunk = texts[i : i + BATCH_SIZE]
            cleaned = [t.strip() or " " for t in chunk]
            resp = await self._client.embeddings.create(model=self._model, input=cleaned)
            out.extend(item.embedding for item in resp.data)
        return out
