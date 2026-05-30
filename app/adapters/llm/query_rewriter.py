"""LLM Query Rewriter + Reranker — нормалізація і reranking для hybrid search.

Архітектура (стандарт індустрії: Perplexity, Glean, Notion AI):

    Client message
        ↓
    QueryRewriter [bypass → LRU → Redis → LLM] → RewrittenQuery
        ↓
    Hybrid Search (multi-query + RRF) → top 10 candidates
        ↓
    LLMReranker [optional, cached] → top 5 final
        ↓
    Return to user

Захист:
- Schema validation на response
- Fallback на оригінальний query при будь-якій помилці
- Timeout 3s — якщо LLM тупить, не блокуємо клієнта
- Telemetry: hit rates, latency, помилки логуються
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from openai import AsyncOpenAI

from app.core.config import get_settings

log = logging.getLogger(__name__)

# ── Налаштування ──────────────────────────────────────────────────
REWRITER_MODEL = "gpt-4o-mini"  # дешево + швидко (~200-400ms)
REWRITER_TIMEOUT_S = 3.0
REWRITER_MAX_TOKENS = 300
LRU_CACHE_SIZE = 2048
REDIS_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 днів — нормалізації стабільні
REDIS_KEY_PREFIX = "qrw:"  # query rewriter cache

# Reranker
RERANKER_MODEL = "gpt-4o-mini"
RERANKER_TIMEOUT_S = 4.0
RERANKER_MAX_TOKENS = 600
RERANKER_MAX_CANDIDATES = 15  # обмежуємо кількість для контролю latency/cost
RERANKER_REDIS_PREFIX = "qrr:"  # query reranker cache
RERANKER_REDIS_TTL = 60 * 60 * 24 * 7  # 7 днів — каталог змінюється частіше

# Bypass: пропускаємо LLM тільки для коротких 1-2 словних запитів, які
# не містять жаргону/типів помилок. Все інше → нормалізуємо через LLM
# (це 60-80% хіт-рейту через cache, тож вартість мінімальна).
BYPASS_MAX_WORDS = 2

# Слова-індикатори того, що запит точно потребує нормалізації:
# розмовні маркери, жаргон, типові помилки.
NORMALIZE_TRIGGERS = {
    # Розмовні
    "хочу", "хотіл", "хотіла", "хотів", "хочется", "хотел",
    "want", "would", "could", "please", "будь",
    "чи можна", "можна", "можно", "як", "как", "how",
    "записа", "запис", "book", "запиши",
    "поможи", "допоможи", "help",
    "ласка",
    "освіжи", "освежи", "обнови", "оновити",
    "зробити", "зробіть", "сделать", "сделайте",
    "потрібн", "нужн", "треба",
    "робля", "роблю", "делаю", "делать",
    # Жаргон/сленг (мають бути нормалізовані)
    "шеллак", "shellac",  # → гель-лак
    "розчоск", "разрос",  # → укладка
    "айртач", "airtouch",  # → Airtouch
    "балояж", "балаяж", "балаяш",  # → балаяж
    # Типові помилки
    "манекюр", "манікур", "маникур",
    "педикур", "педікур",
}


def _has_trigger(query_lower: str) -> bool:
    """True якщо запит містить trigger, що вимагає LLM нормалізації."""
    for marker in NORMALIZE_TRIGGERS:
        if marker in query_lower:
            return True
    return False


@dataclass
class RewrittenQuery:
    """Результат rewrite-а: primary + альтернативи + structured filters."""
    primary: str  # основна нормалізована форма
    alternatives: list[str] = field(default_factory=list)  # 0-3 додаткові варіанти
    length: int | None = None  # 1-6
    level: str | None = None  # МАЙСТЕР / ТОП / АРТ / БАРБЕР
    gender: str | None = None  # male / female
    cached: bool = False  # для telemetry

    @classmethod
    def from_raw(cls, query: str) -> "RewrittenQuery":
        """Fallback: повертаємо як є, без LLM."""
        return cls(primary=query)

    def all_queries(self) -> list[str]:
        """Унікальний список запитів для retrieval (primary + alternatives)."""
        seen: set[str] = set()
        out: list[str] = []
        for q in [self.primary, *self.alternatives]:
            key = q.lower().strip()
            if key and key not in seen:
                seen.add(key)
                out.append(q)
        return out

    def to_json(self) -> str:
        return json.dumps({
            "primary": self.primary,
            "alternatives": self.alternatives,
            "length": self.length,
            "level": self.level,
            "gender": self.gender,
        }, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "RewrittenQuery":
        data = json.loads(raw)
        return cls(
            primary=data.get("primary", ""),
            alternatives=data.get("alternatives", []) or [],
            length=data.get("length"),
            level=data.get("level"),
            gender=data.get("gender"),
            cached=True,
        )


# ── Bypass logic ──────────────────────────────────────────────────

def should_bypass(query: str) -> bool:
    """True — пропускаємо LLM, шукаємо як є.

    Bypass тільки якщо:
    - 1-2 слова І не містить trigger-маркерів жаргону/помилок.
    Все інше → LLM (з кешем це дешево).
    """
    q = (query or "").strip()
    if not q or len(q) < 2:
        return True

    q_lower = q.lower()
    if _has_trigger(q_lower):
        return False

    words = q_lower.split()
    return len(words) <= BYPASS_MAX_WORDS


# ── Cache: LRU + Redis ────────────────────────────────────────────

class _LRUCache:
    """Простий thread-unsafe generic LRU (для async — достатньо)."""

    def __init__(self, max_size: int) -> None:
        self._max = max_size
        self._data: OrderedDict[str, object] = OrderedDict()

    def get(self, key: str) -> object | None:
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        item = self._data[key]
        # Для RewrittenQuery повертаємо копію з cached=True
        if isinstance(item, RewrittenQuery):
            return RewrittenQuery(
                primary=item.primary,
                alternatives=list(item.alternatives),
                length=item.length,
                level=item.level,
                gender=item.gender,
                cached=True,
            )
        return item

    def set(self, key: str, value: object) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        if len(self._data) > self._max:
            self._data.popitem(last=False)

    def __len__(self) -> int:
        return len(self._data)


def _cache_key(query: str) -> str:
    """Канонізуємо key: lowercase + collapsed whitespace + hash."""
    canon = re.sub(r"\s+", " ", (query or "").lower().strip())
    if not canon:
        return ""
    return hashlib.sha1(canon.encode("utf-8")).hexdigest()[:16]


# ── LLM prompt ────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a query normalizer for a beauty salon search engine.

Client messages contain typos, slang, casual phrasing, and may be in Ukrainian, \
Russian, English, or Polish. Your job: convert the message into a canonical \
search query that matches official service names.

Rules:
1. PRIMARY: the most likely service name in canonical form. Use the language \
of the original message. Keep brand names (Lebel, Olaplex, K18, Airtouch) as-is.
2. ALTERNATIVES: 0-3 additional phrasings or related services. \
Use ONLY when the primary is ambiguous or there are common synonyms. \
Empty list is fine.
3. STRUCTURED FILTERS (return null if not mentioned):
   - length: hair length 1-6 ("1 довжина", "довжина 1", "length 3", etc.)
   - level: МАЙСТЕР / ТОП / АРТ / БАРБЕР (level of stylist)
   - gender: male / female (only if explicitly stated)
4. Fix obvious typos: манекюр→манікюр, шеллак→гель-лак, балояж→балаяж.
5. Expand jargon: шеллак→гель-лак, нарощування→нарощування нігтів (if context).
6. NEVER invent services. If unclear, return the cleaned-up original.

Output ONLY valid JSON matching this exact schema:
{"primary": "...", "alternatives": [...], "length": null, "level": null, "gender": null}

Examples:
Input: "хочу зробити манекюр"
Output: {"primary": "манікюр", "alternatives": [], "length": null, "level": null, "gender": null}

Input: "шеллак на довгі нігті"
Output: {"primary": "гель-лак", "alternatives": ["шеллак"], "length": null, "level": null, "gender": null}

Input: "освіжити стрижку"
Output: {"primary": "стрижка", "alternatives": ["підрівнювання волосся"], "length": null, "level": null, "gender": null}

Input: "мужская стрижка топ майстер 1 длина"
Output: {"primary": "чоловіча стрижка", "alternatives": ["мужская стрижка"], "length": 1, "level": "ТОП", "gender": "male"}

Input: "блонд айртач довге волосся"
Output: {"primary": "Airtouch блонд", "alternatives": ["мелірування Airtouch"], "length": 4, "level": null, "gender": null}
"""


# ── Rewriter ──────────────────────────────────────────────────────

class QueryRewriter:
    """LLM-based query normalizer with cache and bypass.

    Usage:
        rewriter = QueryRewriter(redis=redis_client)
        result = await rewriter.rewrite("хочу зробити манекюр")
        # result.primary == "манікюр"
        # result.cached == True (для повторного виклику)
    """

    def __init__(self, redis=None) -> None:
        settings = get_settings()
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._redis = redis
        self._lru = _LRUCache(LRU_CACHE_SIZE)
        # Telemetry
        self._stats = {
            "calls": 0,
            "bypass": 0,
            "lru_hits": 0,
            "redis_hits": 0,
            "llm_calls": 0,
            "llm_errors": 0,
        }

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    async def rewrite(self, query: str) -> RewrittenQuery:
        """Основний метод. Гарантовано повертає RewrittenQuery (ніколи не падає)."""
        self._stats["calls"] += 1

        if not query or not query.strip():
            return RewrittenQuery.from_raw(query or "")

        # 1. Bypass: очевидні запити йдуть напряму
        if should_bypass(query):
            self._stats["bypass"] += 1
            return RewrittenQuery.from_raw(query.strip())

        cache_key = _cache_key(query)

        # 2. In-memory LRU
        cached = self._lru.get(cache_key)
        if cached:
            self._stats["lru_hits"] += 1
            return cached

        # 3. Redis (якщо доступний)
        if self._redis is not None:
            try:
                raw = await self._redis.get(REDIS_KEY_PREFIX + cache_key)
                if raw:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    result = RewrittenQuery.from_json(raw)
                    self._stats["redis_hits"] += 1
                    self._lru.set(cache_key, result)
                    return result
            except Exception as exc:
                log.warning("Redis read failed in rewriter: %s", exc)

        # 4. LLM call
        self._stats["llm_calls"] += 1
        try:
            result = await asyncio.wait_for(
                self._llm_rewrite(query),
                timeout=REWRITER_TIMEOUT_S,
            )
        except (asyncio.TimeoutError, Exception) as exc:
            self._stats["llm_errors"] += 1
            log.warning("Query rewrite failed for %r: %s", query[:80], exc)
            return RewrittenQuery.from_raw(query.strip())

        # 5. Cache result
        self._lru.set(cache_key, result)
        if self._redis is not None:
            try:
                await self._redis.set(
                    REDIS_KEY_PREFIX + cache_key,
                    result.to_json(),
                    ex=REDIS_TTL_SECONDS,
                )
            except Exception as exc:
                log.warning("Redis write failed in rewriter: %s", exc)

        return result

    async def _llm_rewrite(self, query: str) -> RewrittenQuery:
        """Робить LLM call і парсить response. Може кинути exception."""
        t0 = time.time()
        resp = await self._client.chat.completions.create(
            model=REWRITER_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=REWRITER_MAX_TOKENS,
            temperature=0.1,
        )
        latency_ms = int((time.time() - t0) * 1000)
        log.debug("Query rewriter LLM call: %dms for %r", latency_ms, query[:60])

        content = (resp.choices[0].message.content or "").strip()
        if not content:
            raise ValueError("Empty LLM response")

        data = json.loads(content)
        return _validate_response(data, fallback=query)


# ── Response validation ───────────────────────────────────────────

_VALID_LEVELS = {"МАЙСТЕР", "ТОП", "АРТ", "БАРБЕР"}
_VALID_GENDERS = {"male", "female"}


def _validate_response(data: dict, fallback: str) -> RewrittenQuery:
    """Перевіряє і нормалізує LLM response. Будь-які зайві поля ігноруються."""
    if not isinstance(data, dict):
        return RewrittenQuery.from_raw(fallback)

    primary = data.get("primary")
    if not isinstance(primary, str) or not primary.strip():
        primary = fallback.strip()

    alternatives_raw = data.get("alternatives") or []
    alternatives: list[str] = []
    if isinstance(alternatives_raw, list):
        for a in alternatives_raw[:3]:  # max 3
            if isinstance(a, str) and a.strip() and a.strip().lower() != primary.lower():
                alternatives.append(a.strip())

    length_raw = data.get("length")
    length: int | None = None
    if isinstance(length_raw, int) and 1 <= length_raw <= 6:
        length = length_raw
    elif isinstance(length_raw, str) and length_raw.isdigit():
        n = int(length_raw)
        if 1 <= n <= 6:
            length = n

    level_raw = data.get("level")
    level: str | None = None
    if isinstance(level_raw, str):
        upper = level_raw.upper().strip()
        if upper in _VALID_LEVELS:
            level = upper

    gender_raw = data.get("gender")
    gender: str | None = None
    if isinstance(gender_raw, str) and gender_raw.lower() in _VALID_GENDERS:
        gender = gender_raw.lower()

    return RewrittenQuery(
        primary=primary.strip(),
        alternatives=alternatives,
        length=length,
        level=level,
        gender=gender,
        cached=False,
    )


# ══════════════════════════════════════════════════════════════════
# LLM-based Reranker (variant 3)
# ══════════════════════════════════════════════════════════════════
# Cross-encoder approach без окремої моделі: GPT-4o-mini читає (query, candidates)
# і повертає впорядкований список ID. Це дає семантичне розуміння типу
# "освіжити стрижку ≠ стрижка чубчика" — те, що embeddings/IDF не вирішують.
#
# Використання — ОПЦІЙНО, тільки коли:
# - запит коротший за N токенів і неоднозначний
# - candidate set має конфлікти (специфікації + базові)
# - готовий доплатити ~$0.0005 і ~300ms на запит за +5-10% accuracy

_RERANKER_SYSTEM_PROMPT = """You are a search reranker for a beauty salon catalog.

You receive: client query + numbered list of candidate services. Your job: pick \
services that best match the client's intent and return their NUMBERS in best-to-worst order.

Rules:
1. PRIORITIZE intent over surface tokens: "освіжити стрижку" means standard \
women's/men's haircut (refresh existing cut), NOT "стрижка чубчика" (bangs trim) \
or "стрижка машинкою" (clipper haircut).
2. Generic queries → prefer base services. "стрижка" → "Стрижка жіноча/чоловіча", \
NOT "Стрижка чубчика".
3. Specific queries → prefer exact matches. "стрижка чубчика" → "Стрижка чубчика".
4. Brand names matter: "Olaplex" only matches Olaplex services.
5. Length/level qualifiers in query (1 довжина, ТОП) — if mentioned, prefer matching.

Output ONLY valid JSON:
{"ranked": [<num>, <num>, ...]}

Include up to 10 best matches as integers (the numbers from the list). \
If candidate is irrelevant — exclude its number.
"""


@dataclass
class RerankCandidate:
    """Один кандидат для reranker — мінімум полів."""
    id: str
    name: str  # відображувана назва (uk або original)


class LLMReranker:
    """LLM-based reranker для top-K кандидатів після hybrid search.

    Optional: вмикається через `enabled=True` в конструкторі або per-call.
    Кешує результати в Redis по (query, sorted_candidate_ids).
    """

    def __init__(self, redis=None, enabled: bool = True) -> None:
        settings = get_settings()
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._redis = redis
        self._enabled = enabled
        self._lru = _LRUCache(LRU_CACHE_SIZE)
        self._stats = {
            "calls": 0,
            "skipped": 0,
            "lru_hits": 0,
            "redis_hits": 0,
            "llm_calls": 0,
            "llm_errors": 0,
        }

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    async def rerank(
        self,
        query: str,
        candidates: list[RerankCandidate],
        top_k: int = 10,
    ) -> list[str]:
        """Повертає список id-кандидатів, відсортованих за релевантністю.

        Гарантовано не падає: при будь-якій помилці — повертає original order.
        """
        self._stats["calls"] += 1

        if not self._enabled or not candidates or not query.strip():
            self._stats["skipped"] += 1
            return [c.id for c in candidates[:top_k]]

        # Cache key: query + canonical candidate set (sorted ids)
        cand_ids_sorted = sorted(c.id for c in candidates)
        ck_payload = query.lower().strip() + "|" + "|".join(cand_ids_sorted)
        cache_key = hashlib.sha1(ck_payload.encode("utf-8")).hexdigest()[:20]

        # In-memory LRU
        cached_lru = self._lru.get(cache_key)
        if cached_lru is not None:
            self._stats["lru_hits"] += 1
            # Restore order
            return self._reorder(cached_lru, candidates, top_k)

        # Redis cache
        if self._redis is not None:
            try:
                raw = await self._redis.get(RERANKER_REDIS_PREFIX + cache_key)
                if raw:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    cached_ids = json.loads(raw)
                    self._stats["redis_hits"] += 1
                    self._lru.set(cache_key, _CachedRerank(ids=cached_ids))
                    return self._reorder(_CachedRerank(ids=cached_ids), candidates, top_k)
            except Exception as exc:
                log.warning("Redis read failed in reranker: %s", exc)

        # LLM call
        self._stats["llm_calls"] += 1
        try:
            ranked_ids = await asyncio.wait_for(
                self._llm_rerank(query, candidates),
                timeout=RERANKER_TIMEOUT_S,
            )
        except (asyncio.TimeoutError, Exception) as exc:
            self._stats["llm_errors"] += 1
            log.warning("LLM rerank failed for %r: %s", query[:60], exc)
            return [c.id for c in candidates[:top_k]]

        # Cache
        cached_obj = _CachedRerank(ids=ranked_ids)
        self._lru.set(cache_key, cached_obj)
        if self._redis is not None:
            try:
                await self._redis.set(
                    RERANKER_REDIS_PREFIX + cache_key,
                    json.dumps(ranked_ids),
                    ex=RERANKER_REDIS_TTL,
                )
            except Exception as exc:
                log.warning("Redis write failed in reranker: %s", exc)

        return self._reorder(cached_obj, candidates, top_k)

    def _reorder(
        self,
        cached: "_CachedRerank",
        candidates: list[RerankCandidate],
        top_k: int,
    ) -> list[str]:
        """Сортуємо candidates по cached.ids, доповнюємо хвостом якщо кешу не вистачає."""
        cand_set = {c.id for c in candidates}
        order = [cid for cid in cached.ids if cid in cand_set]
        # Додаємо ті, що були в candidates але не в LLM response
        seen = set(order)
        for c in candidates:
            if c.id not in seen:
                order.append(c.id)
                seen.add(c.id)
        return order[:top_k]

    async def _llm_rerank(
        self, query: str, candidates: list[RerankCandidate]
    ) -> list[str]:
        """Виклик LLM для reranking. Може кинути exception.

        Використовуємо числові індекси замість UUID-ів — економія токенів.
        """
        # Обмежуємо кількість кандидатів для контролю latency/cost
        candidates = candidates[:RERANKER_MAX_CANDIDATES]

        # Numbered list — індекси замість id для economия токенів
        numbered = "\n".join(
            f"{i+1}. {c.name}" for i, c in enumerate(candidates)
        )
        user_msg = f"Client query: {query}\n\nCandidates:\n{numbered}"

        t0 = time.time()
        resp = await self._client.chat.completions.create(
            model=RERANKER_MODEL,
            messages=[
                {"role": "system", "content": _RERANKER_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=RERANKER_MAX_TOKENS,
            temperature=0.0,
        )
        latency_ms = int((time.time() - t0) * 1000)
        log.debug("LLM reranker call: %dms (%d candidates)", latency_ms, len(candidates))

        content = (resp.choices[0].message.content or "").strip()
        if not content:
            raise ValueError("Empty LLM rerank response")

        data = json.loads(content)
        ranked_nums = data.get("ranked", [])
        if not isinstance(ranked_nums, list):
            raise ValueError("ranked is not a list")

        # Маппимо числа → id, валідуємо range
        valid_ids: list[str] = []
        seen: set[str] = set()
        for num in ranked_nums:
            try:
                idx = int(num) - 1  # 1-based → 0-based
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(candidates):
                cid = candidates[idx].id
                if cid not in seen:
                    valid_ids.append(cid)
                    seen.add(cid)

        if not valid_ids:
            # LLM повернув щось дивне — fallback на оригінальний порядок
            return [c.id for c in candidates]
        return valid_ids


@dataclass
class _CachedRerank:
    """Кешований результат: список id у потрібному порядку."""
    ids: list[str]
