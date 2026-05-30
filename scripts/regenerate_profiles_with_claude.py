"""Регенерація всіх service_profile через Claude Sonnet 4.6.

Що НЕ чіпає:
- detailed_description (це ground truth з descServ.json)
- canonical_key, name, profile_id, brand
- embedding (буде оновлено окремим скриптом embed_services)

Що оновлює (для кожної UA-translation):
- short_description, addresses_problems, target_audience, benefits,
  keywords, sales_pitch, cross_sell, procedure_steps, contraindications,
  aftercare_advice

Запуск:
    python -m scripts.regenerate_profiles_with_claude              # dry run, sample 5
    python -m scripts.regenerate_profiles_with_claude --apply      # повний прогон 306
    python -m scripts.regenerate_profiles_with_claude --sample 20  # apply на 20 для тесту
    python -m scripts.regenerate_profiles_with_claude --apply --filter-key ukladka  # тільки конкретний
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re

from anthropic import AsyncAnthropic
from sqlalchemy import select, update

from app.core.config import get_settings
from app.infrastructure.db.models.profile import (
    ServiceProfile,
    ServiceProfileTranslation,
)
from app.infrastructure.db.session import build_engine, build_session_factory
from scripts._profile_expert_prompt import (
    EXPERT_SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
    find_forbidden,
    validate_response,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s | %(message)s")
log = logging.getLogger("regen_profiles")

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1500
CONCURRENCY = 4  # паралельні виклики Claude
BATCH_COMMIT = 10  # commit кожні N успішних
LANGUAGE = "uk"


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict | None:
    """Claude інколи обгортає JSON у markdown. Витягаємо."""
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


async def regenerate_one(
    client: AsyncAnthropic, profile_name: str, detailed_description: str
) -> tuple[dict | None, list[str]]:
    """Виклик Claude для одного профілю. Повертає (data, errors)."""
    user_text = USER_PROMPT_TEMPLATE.format(
        service_name=profile_name,
        description=detailed_description[:3500],  # safety cap
    )

    try:
        resp = await client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": EXPERT_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_text}],
        )
    except Exception as exc:  # noqa: BLE001
        return None, [f"api_error: {exc}"]

    content = "".join(b.text for b in resp.content if b.type == "text")
    data = _extract_json(content)
    if data is None:
        return None, [f"json_parse_error: {content[:200]!r}"]

    errors = validate_response(data)
    return data, errors


async def process_profile(
    session_factory,
    client: AsyncAnthropic,
    profile_id: str,
    profile_name: str,
    detailed_description: str,
    apply: bool,
    semaphore: asyncio.Semaphore,
) -> tuple[str, bool, list[str]]:
    """Регенерує translation одного профілю. Return (profile_id, ok, errors)."""
    async with semaphore:
        data, errors = await regenerate_one(client, profile_name, detailed_description)

    if errors and not data:
        return profile_id, False, errors
    if errors:
        log.warning("[%s] validation errors: %s", profile_id[:8], errors)
        # Поля з forbidden words пропускаємо — лишається старе значення.
        # Решта валідних полів оновлюємо.

    if not apply:
        return profile_id, True, errors

    async with session_factory() as session:
        tr = (
            await session.execute(
                select(ServiceProfileTranslation).where(
                    ServiceProfileTranslation.profile_id == profile_id,
                    ServiceProfileTranslation.language == LANGUAGE,
                )
            )
        ).scalar_one_or_none()

        if not tr:
            return profile_id, False, ["no_uk_translation"]

        # Оновлюємо тільки валідні поля (не ті що з forbidden words)
        bad_fields = {
            e.split(":")[0].strip() for e in errors if ":" in e
        }

        def safe_set(field: str, value):
            if field in bad_fields:
                return
            setattr(tr, field, value)

        safe_set("short_description", data.get("short_description") or tr.short_description)
        safe_set("addresses_problems", data.get("addresses_problems") or [])
        safe_set("target_audience", data.get("target_audience") or [])
        safe_set("benefits", data.get("benefits") or [])
        safe_set("keywords", data.get("keywords") or [])
        safe_set("sales_pitch", data.get("sales_pitch"))
        safe_set("cross_sell", data.get("cross_sell") or [])
        safe_set("procedure_steps", data.get("procedure_steps") or [])
        safe_set("contraindications", data.get("contraindications") or [])
        safe_set("aftercare_advice", data.get("aftercare_advice"))

        await session.commit()

    return profile_id, True, errors


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--sample", type=int, default=None,
                        help="Обробити лише N профілів (для тесту)")
    parser.add_argument("--filter-key", default=None,
                        help="Тільки профілі з конкретним canonical_key")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.anthropic_api_key:
        log.error("ANTHROPIC_API_KEY не встановлено в .env")
        return

    client = AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=2)

    engine = build_engine()
    session_factory = build_session_factory(engine)

    try:
        async with session_factory() as session:
            stmt = (
                select(ServiceProfile, ServiceProfileTranslation)
                .join(
                    ServiceProfileTranslation,
                    ServiceProfileTranslation.profile_id == ServiceProfile.id,
                )
                .where(ServiceProfileTranslation.language == LANGUAGE)
            )
            if args.filter_key:
                stmt = stmt.where(ServiceProfile.canonical_key == args.filter_key)

            rows = list((await session.execute(stmt)).all())

        if args.sample:
            rows = rows[: args.sample]

        if not args.apply:
            rows = rows[:5]  # dry-run default sample

        log.info("Profiles to process: %d (apply=%s)", len(rows), args.apply)

        semaphore = asyncio.Semaphore(CONCURRENCY)
        tasks = [
            process_profile(
                session_factory,
                client,
                profile.id,
                profile.name,
                tr.detailed_description or "",
                args.apply,
                semaphore,
            )
            for profile, tr in rows
        ]

        results = []
        success = 0
        failed = 0
        with_warnings = 0

        for i, coro in enumerate(asyncio.as_completed(tasks), 1):
            pid, ok, errors = await coro
            results.append((pid, ok, errors))
            if ok and not errors:
                success += 1
            elif ok and errors:
                with_warnings += 1
            else:
                failed += 1
                log.error("FAILED %s: %s", pid[:8], errors)
            if i % 10 == 0:
                log.info("Progress: %d / %d (ok=%d warn=%d fail=%d)",
                         i, len(tasks), success, with_warnings, failed)

        log.info("DONE. ok=%d warnings=%d failed=%d", success, with_warnings, failed)

        # Sample preview якщо dry-run
        if not args.apply and rows:
            log.info("\n=== Dry-run sample (first profile) ===")
            first_profile, _ = rows[0]
            data, _ = await regenerate_one(
                client, first_profile.name, rows[0][1].detailed_description or ""
            )
            if data:
                print(json.dumps(data, ensure_ascii=False, indent=2))

    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
