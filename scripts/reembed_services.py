"""Re-embed послуг після перекладу.

Будує embed-текст з name_uk|name_ru|name_en|name_pl і оновлює name_embedding.
Батчами по 100 (ліміт OpenAI), з concurrency для швидкості.

Запуск:
    python -m scripts.reembed_services
    python -m scripts.reembed_services --country ua
"""
from __future__ import annotations

import argparse
import asyncio
import time

from sqlalchemy import select, update

from app.adapters.embeddings.openai_embedder import OpenAIEmbedder
from app.infrastructure.db.models.catalog import Service
from app.infrastructure.db.session import build_engine, build_session_factory, country_session

BATCH = 100  # OpenAI batch limit
CONCURRENT = 5  # паралельних embed-запитів


def build_embed_text(uk: str, ru: str, en: str, pl: str) -> str:
    return " | ".join(p for p in (uk, ru, en, pl) if p and p.strip())


async def embed_chunk(embedder: OpenAIEmbedder, texts: list[str]) -> list[list[float]]:
    return await embedder.embed_batch(texts)


async def process_country(country: str, factory, embedder: OpenAIEmbedder) -> int:
    async with country_session(factory, country) as session:
        stmt = select(Service).where(
            Service.archive.is_(False),
            Service.name_uk.isnot(None),
        )
        services = list((await session.execute(stmt)).scalars().all())

    total = len(services)
    print(f"[{country}] {total} services to embed", flush=True)
    if not total:
        return 0

    done = 0
    t0 = time.time()

    # Готуємо всі тексти
    all_texts = []
    for svc in services:
        all_texts.append(build_embed_text(
            svc.name_uk or svc.name,
            svc.name_ru or "",
            svc.name_en or "",
            svc.name_pl or "",
        ))

    # Розбиваємо на батчі
    batches = [all_texts[i:i + BATCH] for i in range(0, total, BATCH)]
    svc_batches = [services[i:i + BATCH] for i in range(0, total, BATCH)]

    # Embed з concurrency
    for ci in range(0, len(batches), CONCURRENT):
        concurrent_batches = batches[ci:ci + CONCURRENT]
        concurrent_svcs = svc_batches[ci:ci + CONCURRENT]

        tasks = [embed_chunk(embedder, b) for b in concurrent_batches]
        results = await asyncio.gather(*tasks)

        # Зберігаємо в БД
        async with country_session(factory, country) as session:
            for svcs, vectors in zip(concurrent_svcs, results):
                for svc, vec in zip(svcs, vectors):
                    await session.execute(
                        update(Service).where(Service.id == svc.id).values(
                            name_embedding=vec
                        )
                    )

        batch_done = sum(len(b) for b in concurrent_batches)
        done += batch_done
        elapsed = time.time() - t0
        rate = done / elapsed if elapsed > 0 else 0
        eta = int((total - done) / rate) if rate > 0 else 0
        pct = done * 100 // total
        print(f"[{country}] {done}/{total} ({pct}%) — {rate:.1f} svc/s — ETA {eta}s", flush=True)

    return done


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", choices=["ua", "pl", "gb"])
    args = parser.parse_args()

    countries = [args.country] if args.country else ["ua", "pl", "gb"]
    embedder = OpenAIEmbedder()
    engine = build_engine()
    factory = build_session_factory(engine)
    total = 0
    t0 = time.time()
    try:
        for c in countries:
            total += await process_country(c, factory, embedder)
    finally:
        await engine.dispose()
    print(f"DONE. {total} services re-embedded in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    asyncio.run(amain())
