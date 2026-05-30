"""Великий тест якості vector-пошуку послуг.

Стратегія:
1. Беремо ВСІ послуги салону (без архівних, без add-on).
2. Групуємо за 'family' — прибираючи суфікси розмірів типу '(Short)', '(Long)',
   '(Medium)', '(Extra long)', '(Tailbone length)', '(Дуже довге волосся)' тощо.
   Це бо клієнт у природному запиті не пише розмір — а в БД одна послуга має 5 рядків.
3. Для кожної family витягуємо clean назви UA / RU / EN з трилінгвальних рядків.
4. Запускаємо search для кожного варіанту → перевіряємо чи top-K містить хоча б одну
   послугу з ТІЄЇ Ж family.
5. Збираємо звіт.

Запуск:
    python -m scripts.test_search_quality --country gb
    python -m scripts.test_search_quality --country gb --top-k 5 --csv report.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import re
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select

from app.adapters.embeddings.openai_embedder import OpenAIEmbedder
from app.infrastructure.db.models.catalog import Service
from app.infrastructure.db.models.staff import Salon
from app.infrastructure.db.repositories.service_repo import ServiceRepository
from app.infrastructure.db.session import build_engine, build_session_factory, country_session

logging.basicConfig(level=logging.WARNING)

# Регексп для прибирання суфіксів розмірів. Усі ловимо парними дужками.
_SIZE_SUFFIX = re.compile(
    r"\s*\(("
    r"Short|Medium|Long|Extra Long|Extra long|Tailbone length|Junior|TOP|"
    r"Коротке волосся|Середнє волосся|Довге волосся|Дуже довге волосся|"
    r"Довжина до куприка|"
    r"Короткие волосы|Средние волосы|Длинные волосы|Очень длинные волосы|"
    r"Длина до копчика"
    r")\)\s*",
    re.IGNORECASE,
)

# Витягуємо мовну частину з 'EN ... / UA ... / RUS ...'.
_LANG_PART = re.compile(
    r"(?:^|/)\s*(?P<lang>EN|UA|RUS?|PL)\s+(?P<text>[^/]+?)(?=\s*/|$)",
    re.IGNORECASE,
)

ADDON_KEYWORDS = ("add-on", "addon", "(дополни", "(додатк", "(доповн")


def is_addon(name: str) -> bool:
    n = name.lower()
    return any(k in n for k in ADDON_KEYWORDS)


def strip_size_suffix(name: str) -> str:
    return _SIZE_SUFFIX.sub("", name).strip()


def parse_lang_parts(name: str) -> dict[str, str]:
    """{'EN': 'Root colouring', 'UA': 'Фарбування кореня', 'RUS': 'Окрашивание корней'}"""
    result: dict[str, str] = {}
    for m in _LANG_PART.finditer(name):
        lang = m.group("lang").upper()
        if lang == "RU":
            lang = "RUS"
        text = m.group("text").strip().rstrip("/").strip()
        if text and lang not in result:
            result[lang] = text
    return result


@dataclass
class FamilyTest:
    family_key: str  # canonical name
    member_ids: set[str]  # всі id-послуги в family
    queries: list[tuple[str, str]]  # (lang_label, query_text)


def build_family_index(services: list[Service]) -> dict[str, FamilyTest]:
    families: dict[str, FamilyTest] = {}
    for svc in services:
        if is_addon(svc.name):
            continue
        canonical = strip_size_suffix(svc.name)
        if canonical not in families:
            queries: list[tuple[str, str]] = []

            # 1. Спочатку пробуємо CRM-формат (EN ... / UA ... / RUS ...)
            parts = parse_lang_parts(canonical)
            for lang in ("UA", "RUS", "EN"):
                if lang in parts:
                    queries.append((lang, parts[lang]))

            # 2. Якщо CRM-формату немає — використовуємо name_uk/name_ru/name_en/name_pl
            if not queries:
                # Беремо будь-яку першу послугу як джерело перекладів
                uk = strip_size_suffix(svc.name_uk or "").strip()
                ru = strip_size_suffix(svc.name_ru or "").strip()
                en = strip_size_suffix(svc.name_en or "").strip()
                pl = strip_size_suffix(svc.name_pl or "").strip()
                if uk:
                    queries.append(("UA", uk))
                if ru and ru != uk:
                    queries.append(("RU", ru))
                if en and en != uk:
                    queries.append(("EN", en))
                if pl and pl != uk and pl != en:
                    queries.append(("PL", pl))

            # 3. Fallback
            if not queries:
                queries = [("RAW", canonical)]

            families[canonical] = FamilyTest(
                family_key=canonical, member_ids=set(), queries=queries
            )
        families[canonical].member_ids.add(svc.id)
    return families


@dataclass
class QueryResult:
    salon_name: str
    family_key: str
    lang: str
    query: str
    hit: bool
    hit_position: int  # 1-based, 0 if miss
    top_results: list[str]  # імена top-K


async def test_salon(
    session, embedder: OpenAIEmbedder, salon: Salon, top_k: int
) -> tuple[list[QueryResult], int]:
    repo = ServiceRepository(session, embedder=embedder)

    rows = (
        await session.execute(
            select(Service).where(
                Service.salon_id == salon.id,
                Service.archive.is_(False),
                Service.name_embedding.is_not(None),
            )
        )
    ).scalars().all()

    families = build_family_index(list(rows))
    print(f"\n=== {salon.name} ({salon.city}) — {len(rows)} services, {len(families)} unique families")

    results: list[QueryResult] = []
    family_count = len(families)
    for idx, fam in enumerate(families.values(), start=1):
        for lang, query in fam.queries:
            top = await repo.search(salon_id=salon.id, query=query, limit=top_k)
            top_ids = [s.id for s in top]
            top_names = [s.name[:80] for s in top]
            hit = False
            hit_pos = 0
            for pos, svc_id in enumerate(top_ids, start=1):
                if svc_id in fam.member_ids:
                    hit = True
                    hit_pos = pos
                    break
            results.append(
                QueryResult(
                    salon_name=salon.name,
                    family_key=fam.family_key,
                    lang=lang,
                    query=query,
                    hit=hit,
                    hit_position=hit_pos,
                    top_results=top_names,
                )
            )
        if idx % 50 == 0:
            print(f"  ... {idx}/{family_count}")
    return results, len(families)


def summarise(results: list[QueryResult]) -> dict:
    total = len(results)
    hits = sum(1 for r in results if r.hit)
    top1 = sum(1 for r in results if r.hit_position == 1)
    top3 = sum(1 for r in results if 1 <= r.hit_position <= 3)
    by_lang: dict[str, dict] = defaultdict(lambda: {"total": 0, "hit": 0, "top1": 0})
    for r in results:
        by_lang[r.lang]["total"] += 1
        if r.hit:
            by_lang[r.lang]["hit"] += 1
        if r.hit_position == 1:
            by_lang[r.lang]["top1"] += 1
    return {
        "total": total,
        "hits": hits,
        "misses": total - hits,
        "hit_rate": hits / total if total else 0,
        "top1_rate": top1 / total if total else 0,
        "top3_rate": top3 / total if total else 0,
        "by_lang": dict(by_lang),
    }


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", required=True, choices=["ua", "pl", "gb"])
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--csv", default=None, help="Зберегти повний звіт у CSV")
    parser.add_argument("--miss-limit", type=int, default=20, help="Скільки промахів вивести у звіт")
    args = parser.parse_args()

    embedder = OpenAIEmbedder()
    engine = build_engine()
    factory = build_session_factory(engine)

    all_results: list[QueryResult] = []
    async with country_session(factory, args.country) as session:
        salons = (await session.execute(select(Salon).where(Salon.archive.is_(False)))).scalars().all()
        for salon in salons:
            results, _ = await test_salon(session, embedder, salon, top_k=args.top_k)
            all_results.extend(results)

    summary = summarise(all_results)

    print("\n" + "=" * 70)
    print(f"SUMMARY (country={args.country}, top_k={args.top_k})")
    print("=" * 70)
    print(f"Total queries: {summary['total']}")
    print(f"Hits        : {summary['hits']:5d} ({summary['hit_rate']*100:.1f}%)")
    print(f"Misses      : {summary['misses']:5d}")
    print(f"Top-1 rate  : {summary['top1_rate']*100:.1f}%")
    print(f"Top-3 rate  : {summary['top3_rate']*100:.1f}%")
    print("\nBy language:")
    for lang, st in summary["by_lang"].items():
        rate = st["hit"] / st["total"] * 100 if st["total"] else 0
        t1 = st["top1"] / st["total"] * 100 if st["total"] else 0
        print(f"  {lang:5s}  total={st['total']:4d}  hit={st['hit']:4d} ({rate:5.1f}%)  top1={t1:5.1f}%")

    misses = [r for r in all_results if not r.hit]
    if misses:
        print(f"\nFirst {min(len(misses), args.miss_limit)} misses:")
        for r in misses[: args.miss_limit]:
            print(f"\n  [{r.lang}] {r.salon_name}")
            print(f"    family : {r.family_key[:90]}")
            print(f"    query  : {r.query!r}")
            print(f"    top-{args.top_k}:")
            for i, n in enumerate(r.top_results, start=1):
                print(f"      {i}. {n}")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["salon", "lang", "query", "family", "hit", "hit_pos", "top1_name"])
            for r in all_results:
                w.writerow([
                    r.salon_name, r.lang, r.query, r.family_key,
                    "Y" if r.hit else "N", r.hit_position,
                    r.top_results[0] if r.top_results else "",
                ])
        print(f"\nFull report → {args.csv}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
