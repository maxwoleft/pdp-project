"""Embed ServiceProfileTranslation.embedding для всіх translations.

Запуск:
    python -m scripts.embed_translations
    python -m scripts.embed_translations --lang en
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from openai import AsyncOpenAI
from sqlalchemy import select

from app.core.config import get_settings
from app.infrastructure.db.models.profile import ServiceProfileTranslation
from app.infrastructure.db.session import build_engine, build_session_factory

log = logging.getLogger("embed_translations")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def build_text(t: ServiceProfileTranslation) -> str:
    parts = []
    if t.short_description:
        parts.append(t.short_description)
    if t.addresses_problems:
        parts.append("Problems: " + ", ".join(t.addresses_problems))
    if t.target_audience:
        parts.append("For: " + ", ".join(t.target_audience))
    if t.benefits:
        parts.append("Benefits: " + ", ".join(t.benefits))
    if t.keywords:
        parts.append("Keywords: " + ", ".join(t.keywords))
    if t.sales_pitch:
        parts.append(t.sales_pitch)
    return " | ".join(parts)


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lang", default=None)
    args = parser.parse_args()

    settings = get_settings()
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            stmt = select(ServiceProfileTranslation).where(
                ServiceProfileTranslation.embedding.is_(None)
            )
            if args.lang:
                stmt = stmt.where(ServiceProfileTranslation.language == args.lang)
            rows = list((await session.execute(stmt)).scalars().all())
            log.info("To embed: %d", len(rows))
            if not rows:
                return

            sem = asyncio.Semaphore(16)
            done = [0]

            async def embed_one(t: ServiceProfileTranslation) -> None:
                async with sem:
                    text = build_text(t)
                    try:
                        resp = await client.embeddings.create(
                            model="text-embedding-3-small",
                            input=text,
                        )
                        t.embedding = resp.data[0].embedding
                    except Exception as exc:
                        log.warning("embed failed id=%s: %s", t.id, exc)
                    done[0] += 1
                    if done[0] % 50 == 0:
                        log.info("  progress: %d / %d", done[0], len(rows))

            await asyncio.gather(*[embed_one(t) for t in rows])
            await session.commit()
            log.info("DONE: %d / %d embedded.", done[0], len(rows))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
