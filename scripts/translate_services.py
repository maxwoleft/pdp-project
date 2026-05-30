"""Масовий переклад + re-embed. Простий послідовний скрипт.

Для кожного батчу (50 послуг): перекласти → зберегти в БД → embed → зберегти.
Flush після кожного батчу — прогрес видно в БД одразу.

Запуск:
    python -m scripts.translate_services
    python -m scripts.translate_services --country gb
    python -m scripts.translate_services --force
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time

from sqlalchemy import select, update

from app.adapters.embeddings.openai_embedder import OpenAIEmbedder
from app.adapters.translations.service_translator import (
    ALL_LANGS,
    ServiceTranslator,
    detect_source_lang,
    parse_multilingual_name,
)
from app.infrastructure.db.models.catalog import Service
from app.infrastructure.db.session import build_engine, build_session_factory, country_session

BATCH = 200
CONCURRENT_TRANSLATE = 4  # паралельних GPT-запитів


def log(msg: str) -> None:
    print(msg, flush=True)


def build_embed_text(uk: str, ru: str, en: str, pl: str) -> str:
    return " | ".join(p for p in (uk, ru, en, pl) if p and p.strip())


async def process_country(
    country: str, factory, translator: ServiceTranslator, embedder: OpenAIEmbedder, force: bool
) -> int:
    async with country_session(factory, country) as session:
        stmt = select(Service).where(Service.archive.is_(False))
        if not force:
            stmt = stmt.where(Service.name_uk.is_(None))
        services = list((await session.execute(stmt)).scalars().all())

    total = len(services)
    log(f"[{country}] {total} services to translate")
    if not total:
        return 0

    done = 0
    t0 = time.time()

    for i in range(0, total, BATCH):
        chunk = services[i : i + BATCH]

        # 1. Розділяємо на parsed (CRM) і mono (потребують перекладу)
        mono_names: list[str] = []
        mono_indices: list[int] = []
        parsed_missing: list[tuple[int, dict, str, list[str]]] = []  # (idx, trans, src, missing_langs)
        src_lang: str | None = None
        chunk_translations: list[dict[str, str] | None] = [None] * len(chunk)

        for idx, svc in enumerate(chunk):
            parsed = parse_multilingual_name(svc.name)
            if parsed:
                missing = [l for l in ALL_LANGS if not parsed.get(l)]
                if missing:
                    src = next((l for l in ("uk", "en") if parsed.get(l)), None)
                    if src:
                        parsed_missing.append((idx, parsed, src, missing))
                    else:
                        chunk_translations[idx] = parsed
                else:
                    chunk_translations[idx] = parsed
            else:
                src = detect_source_lang(svc.name)
                if src_lang is None:
                    src_lang = src
                mono_names.append(svc.name)
                mono_indices.append(idx)

        # 2. Збираємо всі missing-parsed в один батч (замість по одному)
        missing_names: list[str] = []
        missing_meta: list[tuple[int, dict, list[str]]] = []
        for idx, parsed, src, miss_langs in parsed_missing:
            missing_names.append(parsed[src])
            missing_meta.append((idx, parsed, miss_langs))

        # 3. Паралельний переклад: mono + missing одночасно
        tasks = []
        if mono_names and src_lang:
            target = [l for l in ALL_LANGS if l != src_lang]
            tasks.append(("mono", translator.translate_batch(mono_names, src_lang, target)))
        if missing_names:
            # Визначаємо src для parsed — беремо uk або en
            p_src = parsed_missing[0][2] if parsed_missing else "uk"
            p_target = list(set(l for _, _, _, ml in parsed_missing for l in ml))
            tasks.append(("missing", translator.translate_batch(missing_names, p_src, p_target)))

        task_results = {}
        if tasks:
            gathered = await asyncio.gather(*(t[1] for t in tasks))
            for (label, _), result in zip(tasks, gathered):
                task_results[label] = result

        # 4. Розкладаємо результати mono
        if "mono" in task_results:
            results = task_results["mono"]
            for j, midx in enumerate(mono_indices):
                r = results[j] if j < len(results) else {}
                r[src_lang] = mono_names[j]
                chunk_translations[midx] = r

        # 5. Розкладаємо результати missing-parsed
        if "missing" in task_results:
            results = task_results["missing"]
            for j, (idx, parsed, miss_langs) in enumerate(missing_meta):
                r = results[j] if j < len(results) else {}
                for l in miss_langs:
                    parsed[l] = r.get(l, "")
                chunk_translations[idx] = parsed

        # Fallback
        for idx in range(len(chunk)):
            if chunk_translations[idx] is None:
                chunk_translations[idx] = {"uk": chunk[idx].name}

        # 6. Embed тексти
        embed_texts = []
        svc_ids = []
        for svc, trans in zip(chunk, chunk_translations):
            uk = trans.get("uk", "") or svc.name
            ru = trans.get("ru", "")
            en = trans.get("en", "")
            pl = trans.get("pl", "")
            embed_texts.append(build_embed_text(uk, ru, en, pl))
            svc_ids.append((svc.id, uk, ru, en, pl))

        vectors = await embedder.embed_batch(embed_texts)

        # 7. Save to DB
        async with country_session(factory, country) as session:
            for (sid, uk, ru, en, pl), vec in zip(svc_ids, vectors):
                await session.execute(
                    update(Service).where(Service.id == sid).values(
                        name_uk=uk, name_ru=ru, name_en=en, name_pl=pl, name_embedding=vec
                    )
                )

        done += len(chunk)
        elapsed = time.time() - t0
        rate = done / elapsed if elapsed > 0 else 0
        eta = int((total - done) / rate) if rate > 0 else 0
        pct = done * 100 // total
        log(f"[{country}] {done}/{total} ({pct}%) — {rate:.1f} svc/s — ETA {eta}s")

    return done


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", choices=["ua", "pl", "gb"])
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    countries = [args.country] if args.country else ["ua", "pl", "gb"]
    translator = ServiceTranslator()
    embedder = OpenAIEmbedder()
    engine = build_engine()
    factory = build_session_factory(engine)
    total = 0
    t0 = time.time()
    try:
        for c in countries:
            total += await process_country(c, factory, translator, embedder, args.force)
    finally:
        await engine.dispose()
    log(f"DONE. {total} services in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(amain())
