"""Завантажує сценарії з БД у форматі скрипта eval.run.

Якщо БД недоступна — fallback на hardcoded SCENARIOS зі scenarios.py.
"""
from __future__ import annotations

import logging

from scripts.eval.scenarios import Scenario, SCENARIOS

log = logging.getLogger(__name__)


async def load_scenarios_from_db() -> list[Scenario]:
    """Зчитує сценарії з public.eval_scenario.

    Повертає список Scenario dataclass'ів сумісних з runner-ом.
    Якщо БД пуста або недоступна — повертає hardcoded fallback.
    """
    try:
        from app.infrastructure.db.repositories.eval_repo import EvalScenarioRepository
        from app.infrastructure.db.session import build_engine, build_session_factory

        engine = build_engine()
        factory = build_session_factory(engine)
        try:
            async with factory() as session:
                repo = EvalScenarioRepository(session)
                rows = await repo.list_all(enabled_only=True)
                if not rows:
                    log.warning("DB has no scenarios — using hardcoded fallback")
                    return SCENARIOS
                return [
                    Scenario(
                        id=r.slug,
                        description=r.description,
                        country=r.country,
                        turns=list(r.turns or []),
                        expectations=list(r.expectations or []),
                        must_not=list(r.must_not or []),
                        preset_salon_id=r.preset_salon_id,
                        tags=list(r.tags or []),
                        reference_responses=list(getattr(r, "reference_responses", []) or []),
                    )
                    for r in rows
                ]
        finally:
            await engine.dispose()
    except Exception as exc:
        log.warning("Failed to load scenarios from DB (%s) — falling back to hardcoded", exc)
        return SCENARIOS
