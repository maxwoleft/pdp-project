"""Обчислює embedding для всіх послуг, у яких name_embedding NULL.

Запуск:
    python -m scripts.embed_services                # всі країни
    python -m scripts.embed_services --country gb   # тільки одна
    python -m scripts.embed_services --reembed      # перерахувати ВСІ (навіть існуючі)

Витрати: ~$0.01 одноразово на 14k послуг.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import select, update

from app.adapters.embeddings.openai_embedder import OpenAIEmbedder
from app.infrastructure.db.models.catalog import Service
from app.infrastructure.db.session import build_engine, build_session_factory, country_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")
log = logging.getLogger("embed_services")

BATCH = 100


async def embed_country(country: str, factory, embedder: OpenAIEmbedder, reembed: bool) -> int:
    total = 0
    async with country_session(factory, country) as session:
        stmt = select(Service.id, Service.name).where(Service.archive.is_(False))
        if not reembed:
            stmt = stmt.where(Service.name_embedding.is_(None))
        rows = (await session.execute(stmt)).all()
        log.info("[%s] services to embed: %d", country, len(rows))

        for i in range(0, len(rows), BATCH):
            chunk = rows[i : i + BATCH]
            ids = [r.id for r in chunk]
            names = [r.name for r in chunk]
            try:
                vectors = await embedder.embed_batch(names, normalize_names=True)
            except Exception as exc:  # noqa: BLE001
                log.error("[%s] batch %d failed: %s", country, i, exc)
                continue

            # bulk update
            for sid, vec in zip(ids, vectors):
                await session.execute(
                    update(Service).where(Service.id == sid).values(name_embedding=vec)
                )
            await session.flush()
            total += len(chunk)
            log.info("[%s]   %d / %d", country, total, len(rows))
    return total


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", choices=["ua", "pl", "gb"], default=None)
    parser.add_argument("--reembed", action="store_true",
                        help="Перерахувати ВСІ послуги, навіть з існуючим embedding")
    args = parser.parse_args()

    countries = [args.country] if args.country else ["ua", "pl", "gb"]
    embedder = OpenAIEmbedder()
    engine = build_engine()
    factory = build_session_factory(engine)

    grand_total = 0
    try:
        for c in countries:
            grand_total += await embed_country(c, factory, embedder, reembed=args.reembed)
    finally:
        await engine.dispose()
    log.info("Done. Embedded %d services total.", grand_total)


if __name__ == "__main__":
    asyncio.run(amain())
