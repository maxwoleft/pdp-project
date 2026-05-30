"""Обчислює embedding для всіх ServiceProfileOption з NULL.

Embed з name+short_description+problems+benefits+keywords+pitch — щоб
search_by_concern_v2 матчив клієнтський запит до правильної опції.

Запуск:
    python -m scripts.embed_options
    python -m scripts.embed_options --reembed   # перерахувати ВСЕ
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import select, update

from app.adapters.embeddings.openai_embedder import OpenAIEmbedder
from app.infrastructure.db.models.profile import ServiceProfileOption
from app.infrastructure.db.repositories.profile_repo import ServiceProfileRepository
from app.infrastructure.db.session import build_engine, build_session_factory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")
log = logging.getLogger("embed_options")


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reembed", action="store_true")
    args = parser.parse_args()

    embedder = OpenAIEmbedder()
    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            stmt = select(ServiceProfileOption)
            if not args.reembed:
                stmt = stmt.where(ServiceProfileOption.embedding.is_(None))
            opts = list((await session.execute(stmt)).scalars())
            log.info("Options to embed: %d", len(opts))

            done = 0
            for opt in opts:
                emb_text = ServiceProfileRepository._build_option_embed_text(opt)
                try:
                    vec = await embedder.embed(emb_text)
                except Exception as exc:
                    log.error("Embed failed %s: %s", opt.id, exc)
                    continue
                await session.execute(
                    update(ServiceProfileOption).where(ServiceProfileOption.id == opt.id)
                    .values(embedding=vec)
                )
                done += 1
                if done % 25 == 0:
                    await session.commit()
                    log.info("  committed %d / %d", done, len(opts))
            await session.commit()
            log.info("DONE: %d / %d", done, len(opts))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
