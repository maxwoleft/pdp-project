"""Застосовує згенерований Claude-у-чаті контент до service_profile_translation.

Очікує JSON-файл зі списком профілів, кожен має профіль_id + всі поля.
Перед UPDATE прогоняє validate_response (trigger words, довжини, типи).

Запуск:
    python -m scripts.apply_profile_content .logs/profiles_batch1_content.json
    python -m scripts.apply_profile_content .logs/batch.json --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select

from app.infrastructure.db.models.profile import ServiceProfileTranslation
from app.infrastructure.db.session import build_engine, build_session_factory
from scripts._profile_expert_prompt import validate_response

LANGUAGE = "uk"
APPLY_FIELDS = (
    "short_description",
    "addresses_problems",
    "target_audience",
    "benefits",
    "keywords",
    "sales_pitch",
    "cross_sell",
    "procedure_steps",
    "contraindications",
    "aftercare_advice",
)


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.file.exists():
        print(f"ERROR: {args.file} not found", file=sys.stderr)
        sys.exit(1)

    profiles_data = json.loads(args.file.read_text(encoding="utf-8"))

    engine = build_engine()
    factory = build_session_factory(engine)

    success = 0
    skipped = 0
    failed = 0
    warnings_total = []

    try:
        async with factory() as session:
            for entry in profiles_data:
                pid = entry.get("profile_id")
                if not pid:
                    print(f"SKIP: no profile_id in entry: {entry.get('canonical_key')}")
                    skipped += 1
                    continue

                errors = validate_response(entry)
                if errors:
                    print(f"⚠ {entry.get('canonical_key', pid[:8])}: {errors}")
                    warnings_total.append((entry.get("canonical_key"), errors))

                # Поля з validation errors не оновлюємо
                bad_fields = {e.split(":")[0].strip() for e in errors if ":" in e}

                tr = (
                    await session.execute(
                        select(ServiceProfileTranslation).where(
                            ServiceProfileTranslation.profile_id == pid,
                            ServiceProfileTranslation.language == LANGUAGE,
                        )
                    )
                ).scalar_one_or_none()

                if not tr:
                    print(f"FAIL: no UK translation for {pid[:8]} ({entry.get('canonical_key')})")
                    failed += 1
                    continue

                for field in APPLY_FIELDS:
                    if field in bad_fields:
                        continue
                    if field in entry:
                        setattr(tr, field, entry[field])

                success += 1
                if args.dry_run:
                    print(f"DRY {entry.get('canonical_key')}: would update {pid[:8]}")

            if not args.dry_run:
                await session.commit()
                print(f"\n=== COMMIT: {success} profiles updated ===")
            else:
                print(f"\n=== DRY RUN: would update {success} profiles ===")
    finally:
        await engine.dispose()

    if warnings_total:
        print(f"\nWarnings: {len(warnings_total)}")
        for ck, errs in warnings_total[:5]:
            print(f"  {ck}: {errs}")

    print(f"\nDONE: success={success} skipped={skipped} failed={failed}")


if __name__ == "__main__":
    asyncio.run(amain())
