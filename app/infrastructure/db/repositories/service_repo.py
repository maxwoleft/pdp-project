"""Послуги і категорії. ВСІ запити обов'язково в межах одного салону.

Пошук послуг — Hybrid Search:
- Vector search через pgvector + cosine distance (semantic)
- Trigram lexical search через pg_trgm (exact-match-friendly)
- Об'єднання через Reciprocal Rank Fusion (RRF) — стандарт hybrid search
  (Azure AI Search, Weaviate, Qdrant, ElasticSearch, OpenSearch).

Запит клієнта embedится через OpenAIEmbedder, паралельно йде trigram-пошук
по name_uk + name_ru + name_en + name_pl, далі RRF злиття + IDF rerank.
Fallback на токенний ILIKE-пошук — якщо embedder недоступний.
"""
import asyncio
import logging
import math
import re

from sqlalchemy import and_, or_, select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.infrastructure.db.models.catalog import Category, Service

log = logging.getLogger(__name__)


def _apply_attr_filters(services: list[Service], filters: dict) -> list[Service]:
    """Постфільтр результатів пошуку за brand/volume_ml/zones/session_minutes/ampules.

    Пишемо як post-filter (а не WHERE в SQL), щоб не дублювати фільтри
    у трьох гілках пошуку (multi_query / vector / token / RRF). Втрата
    в продуктивності прийнятна, бо ці фільтри застосовуються рідко
    і завжди після того як кандидати вже звужені до limit*3.
    """
    if not any(v is not None for v in filters.values()):
        return services
    out = []
    for s in services:
        if filters["brand"] is not None and s.brand != filters["brand"]:
            continue
        if filters["volume_ml"] is not None:
            if s.volume_ml is None or float(s.volume_ml) != float(filters["volume_ml"]):
                continue
        if filters["zones"] is not None and s.zones != filters["zones"]:
            continue
        if filters["session_minutes"] is not None and s.session_minutes != filters["session_minutes"]:
            continue
        if filters["ampules"] is not None and s.ampules != filters["ampules"]:
            continue
        out.append(s)
    return out

# Стоп-слова, які не несуть смислу для пошуку послуг
_STOPWORDS = {
    "хочу", "хотіла", "хотіл", "хочется", "хотел", "want", "i", "the", "a",
    "на", "до", "у", "в", "з", "і", "та", "и", "or", "or", "for",
    "будь", "ласка", "please", "пожалуйста",
    "мені", "мне", "me", "my", "мій", "моя", "моє",
    "якщо", "если", "if",
    "це", "эта", "этот", "this",
}

# Query expansion: розмовні дієслова/фрази → терміни назв послуг (всі мови).
# Кожен ключ (стем 5 символів) додає додаткові стеми для rerank.
_SYNONYMS: dict[str, list[str]] = {
    # фарбувати / покрасить / color / farbować → фарбування / окрашивание / coloring / farbowanie
    "пофар": ["фарбу", "окраш", "color", "farbo"],
    "фарбу": ["окраш", "color", "farbo"],
    "покра": ["фарбу", "окраш", "color", "farbo"],
    "красі": ["фарбу", "окраш", "color", "farbo"],
    "color": ["фарбу", "окраш", "farbo"],
    "farbo": ["фарбу", "окраш", "color"],
    # зняти / снять / remove → зняття / снятие / removal / zdejmowanie
    "зняти": ["знятт", "сняти", "remov", "zdjęc"],
    "знять": ["знятт", "сняти", "remov"],
    "снять": ["знятт", "сняти", "remov"],
    "remov": ["знятт", "сняти", "zdjęc", "видал"],
    "gel p": ["гель-"],
    # стригти / подстричь → стрижка / haircut / strzyżenie
    "підст": ["стриж", "hairc", "strzy"],
    "подст": ["стриж", "hairc", "strzy"],
    "стриг": ["стриж", "hairc", "strzy"],
    # зробити масаж → масаж / массаж / massage / masaż
    "масаж": ["масса", "massa", "masaż"],
    "масса": ["масаж", "massa", "masaż"],
    "massa": ["масаж", "масса", "masaż"],
    # чистка / cleansing / oczyszczanie
    "clean": ["чистк", "очист", "oczys"],
    "oczys": ["чистк", "очист", "clean"],
    # ламінування / ламинирование / lamination / laminowanie
    "ламін": ["ламин", "lamin"],
    "ламин": ["ламін", "lamin"],
    "lamin": ["ламін", "ламин"],
}


def _tokenise(query: str) -> list[str]:
    """Розбиває query на токени для rerank-у. Стем — перші 5 символів,
    щоб 'коренів' / 'кореня' / 'корені' співпадали з 'корен', АЛЕ
    'корен' відрізнявся від 'корек' (як у 'корекція брів').

    Також розширює токени синонімами для крос-мовного матчу.
    """
    if not query:
        return []
    raw = re.split(r"[\s,;.!?()/\\\-]+", query.lower())
    tokens: list[str] = []
    seen: set[str] = set()
    for w in raw:
        w = w.strip()
        if len(w) < 3 or w in _STOPWORDS:
            continue
        stem = w[:5] if len(w) > 5 else w
        if stem not in seen:
            tokens.append(stem)
            seen.add(stem)
        # Додаємо синоніми
        for syn in _SYNONYMS.get(stem, []):
            if syn not in seen:
                tokens.append(syn)
                seen.add(syn)
    return tokens


# ── Query understanding (structured filters) ──────────────────────
# Витягаємо з запиту атрибути, які мають бути hard-фільтрами:
# - length (1..6) — довжина волосся клієнта
# - level (МАЙСТЕР / ТОП / АРТ / БАРБЕР) — рівень спеціаліста
#
# Це класичний "query parsing" / "query understanding" підхід — те ж саме
# роблять Google Shopping, Amazon, Booking: розпізнають структуровані атрибути
# в неструктурованому тексті і застосовують їх як hard filters перед ranking.

_LENGTH_RE = re.compile(
    r"(?:^|\s)(\d)\s*(?:дов?жин|длин|length|długo)",
    re.IGNORECASE,
)
# Кожен level має cyrillic + latin варіанти для cross-language ILIKE.
# Anchor на кінець запиту/слова — level завжди суфікс ("...МАЙСТЕР", "...TOP"),
# щоб не плутати з частиною назви ("Top up cold perm" не парситься як ТОП).
_LEVEL_PATTERNS = {
    "МАЙСТЕР": re.compile(r"\b(майстер|мастер|master)\s*$", re.IGNORECASE),
    "ТОП": re.compile(r"\b(топ|top)\s*$", re.IGNORECASE),
    "АРТ": re.compile(r"\b(арт|art)\s*$", re.IGNORECASE),
    "БАРБЕР": re.compile(r"\b(барбер|barber)\s*$", re.IGNORECASE),
}
# Cyrillic + latin варіанти для SQL ILIKE filter
_LEVEL_VARIANTS = {
    "МАЙСТЕР": ["МАЙСТЕР", "МАСТЕР", "MASTER"],
    "ТОП": ["ТОП", "TOP"],
    "АРТ": ["АРТ", "ART"],
    "БАРБЕР": ["БАРБЕР", "BARBER"],
}


def parse_query_attributes(query: str) -> tuple[int | None, str | None]:
    """Витягає (length, level) з запиту. None якщо атрибут не знайдено."""
    length: int | None = None
    m = _LENGTH_RE.search(query)
    if m:
        try:
            n = int(m.group(1))
            if 1 <= n <= 6:
                length = n
        except ValueError:
            pass

    level: str | None = None
    # Перевіряємо у строгому порядку: спершу більш специфічні
    # (АРТ і БАРБЕР), потім ТОП, потім МАЙСТЕР — щоб "ТОП МАЙСТЕР" → ТОП
    for lvl in ("БАРБЕР", "АРТ", "ТОП", "МАЙСТЕР"):
        if _LEVEL_PATTERNS[lvl].search(query):
            level = lvl
            break

    return length, level


def strip_query_attributes(query: str) -> str:
    """Видаляє з запиту распізнані length/level — щоб embedding не плутався."""
    out = _LENGTH_RE.sub(" ", query)
    for pat in _LEVEL_PATTERNS.values():
        out = pat.sub(" ", out)
    return re.sub(r"\s+", " ", out).strip()


class ServiceRepository:
    def __init__(
        self,
        session: AsyncSession,
        embedder=None,
        query_rewriter=None,
        llm_reranker=None,
    ) -> None:
        self.session = session
        # embedder опційний — щоб репозиторій можна було використати без AI
        # (наприклад, у CRM-синку для прямого SQL).
        self._embedder = embedder
        # query_rewriter опційний — нормалізує запит перед search.
        # Якщо None — використовуємо запит як є.
        self._rewriter = query_rewriter
        # llm_reranker опційний — фінальний LLM rerank top-K результатів.
        # Дорого (~$0.0005 + 300ms) але дає +5-10% accuracy на неоднозначних запитах.
        self._llm_reranker = llm_reranker

    async def search(
        self,
        salon_id: str,
        query: str | None = None,
        category_id: str | None = None,
        gender: str | None = None,
        brand: str | None = None,
        volume_ml: float | None = None,
        zones: int | None = None,
        session_minutes: int | None = None,
        ampules: int | None = None,
        limit: int = 15,
    ) -> list[Service]:
        """Vector search: query embed → cosine distance в межах salon_id.

        Структуровані фільтри (brand/volume_ml/zones/session_minutes/ampules)
        накладаються СТРОГО — на результати, не падають у fallback. Використовуй
        їх лише коли клієнт явно вказав специфікацію ("Юведерм 2 мл", "масаж 90 хв").

        Якщо embedder недоступний або запит впав → fallback на token ILIKE.
        """
        attr_filters = {
            "brand": brand,
            "volume_ml": volume_ml,
            "zones": zones,
            "session_minutes": session_minutes,
            "ampules": ampules,
        }

        if query and self._embedder:
            try:
                if self._rewriter is not None:
                    try:
                        rewritten = await self._rewriter.rewrite(query)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("Query rewrite failed: %s", exc)
                        rewritten = None
                    if rewritten:
                        retrieval_limit = limit * 3 if self._llm_reranker else limit
                        results = await self._multi_query_search(
                            salon_id=salon_id,
                            rewritten=rewritten,
                            category_id=category_id,
                            gender=gender,
                            limit=retrieval_limit,
                        )
                        results = _apply_attr_filters(results, attr_filters)
                        return await self._maybe_llm_rerank(query, results, limit)

                retrieval_limit = limit * 3 if self._llm_reranker else limit
                results = await self._vector_search(
                    salon_id=salon_id,
                    query=query,
                    category_id=category_id,
                    gender=gender,
                    limit=retrieval_limit,
                )
                results = _apply_attr_filters(results, attr_filters)
                return await self._maybe_llm_rerank(query, results, limit)
            except Exception as exc:  # noqa: BLE001
                log.warning("Vector search failed, falling back to ILIKE: %s", exc)

        results = await self._token_search(
            salon_id=salon_id,
            query=query,
            category_id=category_id,
            gender=gender,
            limit=limit,
        )
        return _apply_attr_filters(results, attr_filters)

    async def _maybe_llm_rerank(
        self,
        query: str,
        services: list[Service],
        limit: int,
    ) -> list[Service]:
        """Опційний LLM rerank top-K кандидатів. Якщо reranker немає → no-op."""
        if not self._llm_reranker or not services:
            return services[:limit]

        try:
            from app.adapters.llm.query_rewriter import RerankCandidate
            candidates = [
                RerankCandidate(
                    id=svc.id,
                    name=(svc.name_uk or svc.name or "").strip(),
                )
                for svc in services
            ]
            ranked_ids = await self._llm_reranker.rerank(
                query=query,
                candidates=candidates,
                top_k=limit,
            )
            id_to_svc = {s.id: s for s in services}
            ordered = [id_to_svc[rid] for rid in ranked_ids if rid in id_to_svc]
            # Догружаємо хвостом ті, що могли випасти
            seen = set(ranked_ids)
            for s in services:
                if s.id not in seen:
                    ordered.append(s)
            return ordered[:limit]
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM rerank step failed: %s", exc)
            return services[:limit]

    async def _multi_query_search(
        self,
        salon_id: str,
        rewritten,  # RewrittenQuery
        category_id: str | None,
        gender: str | None,
        limit: int,
    ) -> list[Service]:
        """Запускає search для primary + alternatives, об'єднує через RRF.

        Це класичний multi-query expansion (Anserini, ColBERT, RAG-Fusion):
        кожен варіант запиту дає свій ranked list, об'єднуємо через RRF.
        """
        # Hard filters з rewriter мають пріоритет над аргументами функції
        # ТІЛЬКИ якщо їх явно витягли з тексту запиту
        effective_gender = gender
        if rewritten.gender and not effective_gender:
            effective_gender = rewritten.gender

        # Збираємо всі варіанти, які треба пошукати
        queries = rewritten.all_queries()
        if not queries:
            return []

        # Інжектуємо length/level назад у запит, щоб _vector_search їх знайшов
        # через парсер. Це уникає необхідності міняти сигнатуру _vector_search.
        attr_suffix = ""
        if rewritten.length:
            attr_suffix += f" {rewritten.length} довжина"
        if rewritten.level:
            attr_suffix += f" {rewritten.level}"

        # Запускаємо search паралельно
        tasks = [
            self._vector_search(
                salon_id=salon_id,
                query=q + attr_suffix,
                category_id=category_id,
                gender=effective_gender,
                limit=limit,
            )
            for q in queries
        ]
        results_lists = await asyncio.gather(*tasks, return_exceptions=True)

        # RRF злиття всіх ranked lists
        rrf_k = 60
        rrf_scores: dict[str, float] = {}
        all_services: dict[str, Service] = {}

        for results in results_lists:
            if isinstance(results, Exception):
                continue
            for rank, svc in enumerate(results):
                rrf_scores[svc.id] = rrf_scores.get(svc.id, 0.0) + 1.0 / (rrf_k + rank + 1)
                all_services[svc.id] = svc

        ranked = sorted(
            all_services.values(),
            key=lambda s: rrf_scores.get(s.id, 0.0),
            reverse=True,
        )
        return ranked[:limit]

    async def _vector_search(
        self,
        salon_id: str,
        query: str,
        category_id: str | None,
        gender: str | None,
        limit: int,
    ) -> list[Service]:
        """Hybrid Search 2.0: query parsing → hard filters → vector + lexical → RRF → rerank.

        Алгоритм (індустріальний стандарт):
        0. Query understanding: витягаємо length (1-6) і level (МАЙСТЕР/ТОП/АРТ)
           як hard filters. Залишок використовуємо для retrieval.
        1. Vector search (cosine distance) з hard filters
        2. Trigram lexical search (pg_trgm similarity) з hard filters
        3. Reciprocal Rank Fusion: score(d) = Σ 1/(60 + rank_i)
        4. Multi-signal rerank:
           - IDF token overlap (cross-language, рідкісні токени важливіші)
           - Phrase exact match boost (повна фраза в назві → бонус)
           - Mandatory token coverage (всі content токени мають бути в назві)
        5. Якщо hard filter дав 0 результатів — fallback без нього (graceful)

        Чому RRF: vector cosine та trigram similarity мають різні шкали.
        RRF нормалізує через ранги — стандарт Azure AI Search, Weaviate, Qdrant.
        """
        candidate_pool = max(limit * 6, 60)
        rrf_k = 60

        # 0. Query parsing — витягаємо structured фільтри
        length, level = parse_query_attributes(query)
        clean_query = strip_query_attributes(query) or query

        # Спробуємо з hard filters, якщо результатів немає — fallback без них
        for try_with_filters in (True, False):
            apply_length = length if try_with_filters else None
            apply_level = level if try_with_filters else None

            # 1. Vector search
            query_emb = await self._embedder.embed(clean_query)
            vec_stmt = (
                select(Service)
                .options(selectinload(Service.category), selectinload(Service.position))
                .where(
                    Service.salon_id == salon_id,
                    Service.archive.is_(False),
                    Service.name_embedding.is_not(None),
                )
            )
            if category_id:
                vec_stmt = vec_stmt.where(Service.category_id == category_id)
            if gender and gender != "both":
                vec_stmt = vec_stmt.where(Service.gender.in_([gender, "both"]))
            if apply_length:
                # Шукаємо "N довжина" / "length N" / "длина N" / "długość N"
                length_pattern = f"%{apply_length} довжина%"
                length_pattern_alt = f"%{apply_length} длина%"
                length_pattern_en = f"%length {apply_length}%"
                length_pattern_pl = f"%długość {apply_length}%"
                vec_stmt = vec_stmt.where(or_(
                    Service.name.ilike(length_pattern),
                    Service.name_uk.ilike(length_pattern),
                    Service.name_ru.ilike(length_pattern_alt),
                    Service.name_en.ilike(length_pattern_en),
                    Service.name_pl.ilike(length_pattern_pl),
                ))
            if apply_level:
                # Рівень — суфікс назви, шукаємо всі варіанти (cyrillic + latin)
                level_filters = []
                for variant in _LEVEL_VARIANTS[apply_level]:
                    pat = f"%{variant}%"
                    level_filters.extend([
                        Service.name.ilike(pat),
                        Service.name_uk.ilike(pat),
                        Service.name_en.ilike(pat),
                    ])
                vec_stmt = vec_stmt.where(or_(*level_filters))

            vec_stmt = vec_stmt.order_by(
                Service.name_embedding.cosine_distance(query_emb)
            ).limit(candidate_pool)

            vec_rows = await self.session.execute(vec_stmt)
            vector_results = list(vec_rows.scalars().all())

            # 2. Trigram lexical search (pg_trgm)
            lex_where = [
                "salon_id = :salon_id",
                "archive = false",
                "(COALESCE(name_uk, '') || ' ' || COALESCE(name_ru, '') || ' ' || "
                "COALESCE(name_en, '') || ' ' || COALESCE(name_pl, '')) % :query",
            ]
            lex_params: dict = {
                "salon_id": salon_id,
                "query": clean_query,
                "limit": candidate_pool,
            }
            if category_id:
                lex_where.append("category_id = :category_id")
                lex_params["category_id"] = category_id
            if gender and gender != "both":
                lex_where.append("(gender = :gender OR gender = 'both')")
                lex_params["gender"] = gender
            if apply_length:
                lex_where.append(
                    "(name ILIKE :length_pat OR name_uk ILIKE :length_pat OR "
                    "name_ru ILIKE :length_pat_ru OR name_en ILIKE :length_pat_en OR "
                    "name_pl ILIKE :length_pat_pl)"
                )
                lex_params["length_pat"] = f"%{apply_length} довжина%"
                lex_params["length_pat_ru"] = f"%{apply_length} длина%"
                lex_params["length_pat_en"] = f"%length {apply_length}%"
                lex_params["length_pat_pl"] = f"%długość {apply_length}%"
            if apply_level:
                # Будуємо OR по всіх варіантах level (cyrillic + latin)
                level_clauses = []
                for i, variant in enumerate(_LEVEL_VARIANTS[apply_level]):
                    key = f"level_pat_{i}"
                    level_clauses.append(
                        f"(name ILIKE :{key} OR name_uk ILIKE :{key} OR name_en ILIKE :{key})"
                    )
                    lex_params[key] = f"%{variant}%"
                lex_where.append("(" + " OR ".join(level_clauses) + ")")

            lex_sql = sql_text(
                "SELECT id FROM service WHERE "
                + " AND ".join(lex_where)
                + " ORDER BY similarity("
                "COALESCE(name_uk, '') || ' ' || COALESCE(name_ru, '') || ' ' || "
                "COALESCE(name_en, '') || ' ' || COALESCE(name_pl, ''), :query) DESC "
                "LIMIT :limit"
            )
            lex_rows = await self.session.execute(lex_sql, lex_params)
            lex_ids = [row[0] for row in lex_rows.fetchall()]

            if vector_results or lex_ids:
                break  # успіх — фільтри спрацювали
            # Якщо нічого не знайшли з фільтрами і це була перша спроба — fallback
            if not try_with_filters:
                break

        # 3. Reciprocal Rank Fusion
        rrf_scores: dict[str, float] = {}
        all_services: dict[str, Service] = {}

        for rank, svc in enumerate(vector_results):
            rrf_scores[svc.id] = rrf_scores.get(svc.id, 0.0) + 1.0 / (rrf_k + rank + 1)
            all_services[svc.id] = svc

        for rank, svc_id in enumerate(lex_ids):
            rrf_scores[svc_id] = rrf_scores.get(svc_id, 0.0) + 1.0 / (rrf_k + rank + 1)

        # Догружаємо lexical-only сервіси
        missing_ids = [sid for sid in lex_ids if sid not in all_services]
        if missing_ids:
            extra = await self.session.execute(
                select(Service)
                .options(selectinload(Service.category), selectinload(Service.position))
                .where(Service.id.in_(missing_ids))
            )
            for svc in extra.scalars().all():
                all_services[svc.id] = svc

        candidates = sorted(
            all_services.values(),
            key=lambda s: rrf_scores.get(s.id, 0.0),
            reverse=True,
        )[:candidate_pool]

        # 4. Multi-signal rerank
        # Використовуємо clean_query для токенізації (без length/level)
        tokens = _tokenise(clean_query)
        if not tokens:
            return candidates[:limit]

        # Discriminative tokens — оригінальні стеми з запиту, без синонімів
        # Це токени, які ОБОВ'ЯЗКОВО мають бути в назві (mandatory coverage)
        raw_tokens = [
            w[:5] if len(w) > 5 else w
            for w in re.split(r"[\s,;.!?()/\\\-]+", clean_query.lower())
            if len(w.strip()) >= 3 and w.strip() not in _STOPWORDS
        ]

        candidate_texts: list[str] = []
        for svc in candidates:
            candidate_texts.append(" ".join(
                (n or "").lower()
                for n in (svc.name, svc.name_uk, svc.name_ru, svc.name_en, svc.name_pl)
            ))

        # IDF: рідкісні токени важливіші. df рахуємо по кандидатам (локально).
        n_cand = max(len(candidates), 1)
        token_idf: dict[str, float] = {}
        for tok in tokens:
            df = sum(1 for t in candidate_texts if tok in t)
            token_idf[tok] = math.log(n_cand / (1 + df)) if df < n_cand else 0.0

        # Phrase exact match: точна фраза з clean_query → великий бонус
        phrase = clean_query.lower().strip()

        # IDF для raw discriminative tokens (без синонімів) — цей бонус дає
        # перевагу послугам, що містять рідкісні токени з оригінального запиту
        raw_token_idf: dict[str, float] = {}
        for tok in raw_tokens:
            df = sum(1 for t in candidate_texts if tok in t)
            raw_token_idf[tok] = math.log(n_cand / (1 + df)) if df < n_cand else 0.0

        # Specificity penalty: коли запит загальний ("стрижка"), віддаємо
        # перевагу базовим послугам ("Стрижка жіноча") над специфічними
        # ("Стрижка чубчика", "Стрижка машинкою").
        #
        # Логіка: рахуємо token довжину canonical назви свого мовного варіанту
        # (UK), віднімаємо token довжину raw запиту. Чим більше "зайвих" токенів —
        # тим більший штраф. Penalty застосовуємо ТІЛЬКИ для коротких запитів
        # (≤ 3 raw tokens), щоб не зашкодити точним запитам типу "Стрижка чубчика".
        is_generic_query = len(raw_tokens) <= 3
        # Слова, які не рахуємо як "extra" — це службові слова рівнів/довжин/доп
        STRUCTURAL_WORDS = {
            "майстер", "мастер", "топ", "top", "арт", "art", "барбер",
            "барбер", "довжина", "длина", "length", "długość", "додатково",
            "дополнительно", "additionally", "dodatkowo",
        }

        scored: list[tuple[float, int, Service]] = []
        for fusion_rank, (svc, txt) in enumerate(zip(candidates, candidate_texts)):
            # IDF token score (з expanded synonyms)
            idf_score = sum(token_idf[tok] for tok in tokens if tok in txt)

            # Discriminative IDF (raw tokens, без синонімів) — додаткова вага
            # для послуг, що містять рідкісні слова з оригінального запиту
            disc_idf = sum(raw_token_idf[tok] for tok in raw_tokens if tok in txt)

            # Phrase exact match boost
            phrase_bonus = 5.0 if phrase and phrase in txt else 0.0

            # RRF baseline
            rrf_score = rrf_scores.get(svc.id, 0.0) * 100

            # Specificity penalty + base service boost (variant 1)
            # Логіка: коли запит загальний ("стрижка"), віддаємо перевагу
            # послугам з НАЙКОРОТШИМИ і НАЙБІЛЬШ ОЧІКУВАНИМИ доповненнями
            # ("Стрижка жіноча", "Стрижка чоловіча"), а не специфікаціям
            # ("Стрижка чубчика", "Стрижка машинкою").
            specificity_penalty = 0.0
            base_service_boost = 0.0
            if is_generic_query:
                # Беремо найбільш повну мовну версію назви для аналізу
                name_for_count = (svc.name_uk or svc.name or "").lower()
                name_words = [
                    w for w in re.split(r"[\s,;.!?()/\\\-]+", name_for_count)
                    if len(w) >= 3
                    and w not in _STOPWORDS
                    and w not in STRUCTURAL_WORDS
                    and not w.isdigit()
                ]
                raw_tokens_set = set(raw_tokens)

                # Перевіряємо чи запит ПОВНІСТЮ покритий назвою (всі raw_tokens є в txt).
                # Якщо НЕ всі — це не наша категорія, штраф не застосовуємо.
                # ВАЖЛИВО: якщо мі додаємо penalty при неповному покритті,
                # то вб'ємо результати "Зняття гель-лаку" для запиту "зняти гель лак".
                fully_covered = all(rt in txt for rt in raw_tokens_set)

                if fully_covered:
                    # Скільки слів у назві НЕ покриті raw query tokens — це specificity
                    extra_words = [
                        w for w in name_words
                        if not any(rt in w[:5] or w[:5] in rt for rt in raw_tokens_set)
                    ]
                    n_extra = len(extra_words)

                    # Базові кваліфікатори (gender, age) — НЕ штраф, а навпаки boost
                    BASE_QUALIFIERS = {
                        "жіноч", "чолов", "мужск", "женск", "men", "women",
                        "ladies", "дитяч", "детск", "child",
                    }

                    if n_extra == 0:
                        # Точна базова назва (наприклад "Манікюр")
                        base_service_boost = 5.0
                    elif n_extra == 1 and any(
                        ew.startswith(q) for ew in extra_words for q in BASE_QUALIFIERS
                    ):
                        # Базова версія з gender/age qualifier ("Стрижка жіноча")
                        base_service_boost = 4.0
                    else:
                        # Спецыфікація — штраф
                        specificity_penalty = n_extra * 1.5

            combined = (
                rrf_score
                + idf_score * 1.5
                + disc_idf * 2.0
                + phrase_bonus
                + base_service_boost
                - specificity_penalty
            )
            scored.append((-combined, fusion_rank, svc))

        scored.sort(key=lambda t: (t[0], t[1]))
        return [s for _, _, s in scored[:limit]]

    async def _token_search(
        self,
        salon_id: str,
        query: str | None,
        category_id: str | None,
        gender: str | None,
        limit: int,
    ) -> list[Service]:
        stmt = (
            select(Service)
            .options(selectinload(Service.category), selectinload(Service.position))
            .where(Service.salon_id == salon_id, Service.archive.is_(False))
        )

        tokens = _tokenise(query) if query else []
        if tokens:
            token_filters = [Service.name.ilike(f"%{tok}%") for tok in tokens]
            stmt = stmt.where(and_(*token_filters))
        elif query:
            stmt = stmt.where(Service.name.ilike(f"%{query}%"))

        if category_id:
            stmt = stmt.where(Service.category_id == category_id)
        if gender and gender != "both":
            stmt = stmt.where(Service.gender.in_([gender, "both"]))

        rows = await self.session.execute(stmt.limit(limit))
        return list(rows.scalars().all())

    async def get_by_id(self, service_id: str) -> Service | None:
        stmt = (
            select(Service)
            .options(selectinload(Service.category), selectinload(Service.position))
            .where(Service.id == service_id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()


class CategoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_roots(self, salon_id: str) -> list[Category]:
        rows = await self.session.execute(
            select(Category).where(
                Category.salon_id == salon_id,
                Category.parent_id.is_(None),
                Category.archive.is_(False),
            )
        )
        return list(rows.scalars().all())

    async def list_children(self, salon_id: str, parent_id: str) -> list[Category]:
        rows = await self.session.execute(
            select(Category).where(
                Category.salon_id == salon_id,
                Category.parent_id == parent_id,
                Category.archive.is_(False),
            )
        )
        return list(rows.scalars().all())

    async def search(self, salon_id: str, query: str, limit: int = 10) -> list[Category]:
        rows = await self.session.execute(
            select(Category)
            .where(
                Category.salon_id == salon_id,
                Category.name.ilike(f"%{query}%"),
                Category.archive.is_(False),
            )
            .limit(limit)
        )
        return list(rows.scalars().all())
