"""Мігрує hardcoded scenarios з scenarios.py + scenarios_real.py в БД.

Безпечно: за замовчуванням створює тільки нові, не торкається існуючих.
Прапорець --update перезаписує існуючі за slug.

Запуск:
    python -m scripts.seed_scenarios               # створити нові
    python -m scripts.seed_scenarios --update      # оновити всі
"""
from __future__ import annotations

import argparse
import asyncio

from app.infrastructure.db.repositories.eval_repo import EvalScenarioRepository
from app.infrastructure.db.session import build_engine, build_session_factory
from scripts.eval.scenarios import SCENARIOS


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true",
                        help="Оновити існуючі сценарії за slug")
    args = parser.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)

    created = 0
    updated = 0
    skipped = 0

    try:
        async with factory() as session:
            repo = EvalScenarioRepository(session)
            for s in SCENARIOS:
                existing = await repo.get_by_slug(s.id)
                if existing:
                    if args.update:
                        await repo.update(
                            existing.id,
                            description=s.description,
                            country=s.country,
                            turns=list(s.turns),
                            expectations=list(s.expectations),
                            must_not=list(s.must_not),
                            tags=list(s.tags),
                            preset_salon_id=s.preset_salon_id,
                            reference_responses=list(getattr(s, "reference_responses", []) or []),
                            updated_by="seed",
                        )
                        updated += 1
                    else:
                        skipped += 1
                else:
                    await repo.create(
                        slug=s.id,
                        description=s.description,
                        country=s.country,
                        turns=list(s.turns),
                        expectations=list(s.expectations),
                        must_not=list(s.must_not),
                        tags=list(s.tags),
                        preset_salon_id=s.preset_salon_id,
                        reference_responses=list(getattr(s, "reference_responses", []) or []),
                        enabled=True,
                        created_by="seed",
                        updated_by="seed",
                    )
                    created += 1
            await session.commit()
    finally:
        await engine.dispose()

    print(f"DONE: {created} created, {updated} updated, {skipped} skipped (use --update to refresh)")


if __name__ == "__main__":
    asyncio.run(amain())
