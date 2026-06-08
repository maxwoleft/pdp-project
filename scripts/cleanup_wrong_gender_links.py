"""Видалити з gendered/age profiles canonical_keys, що не мають відповідного scope marker.

Запуск:
    python -m scripts.cleanup_wrong_gender_links --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import text

from app.infrastructure.db.session import build_engine, build_session_factory
from scripts.auto_link_missing_keys import (
    GENDER_MARKERS,
    has_scope_marker,
    is_strict_scope,
    profile_scope,
    tokenize,
)


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            r = await session.execute(text(
                "SELECT id, name, country, canonical_keys, key_descriptions "
                "FROM public.service_profile"
            ))
            rows = r.all()
            total_removed = 0
            total_services_unlinked = 0

            for pid, pname, country, ck_list, kd in rows:
                scope = profile_scope(pname or "")
                if not scope:
                    continue
                keys = list(ck_list or [])
                if not keys:
                    continue
                ksets = [tokenize(k) for k in keys]
                # Тільки strict-scope profiles чистимо. Loose profiles (Жіночі укладки)
                # мають generic ckeys без gender marker — це нормально.
                if not is_strict_scope(ksets, scope):
                    continue
                bad: list[str] = []
                good: list[str] = []
                for k in keys:
                    if has_scope_marker(tokenize(k), scope):
                        good.append(k)
                    else:
                        bad.append(k)
                if not bad:
                    continue

                print(f"\n[{pname}] ({country}) scope={scope}")
                for b in bad:
                    print(f"  - remove ckey: {b}")
                total_removed += len(bad)

                # Count affected services
                cnt = (await session.execute(text(
                    f"SELECT COUNT(*) FROM {country}.service "
                    "WHERE profile_id = :pid AND canonical_key = ANY(:keys)"
                ), {"pid": str(pid), "keys": bad})).scalar() or 0
                total_services_unlinked += cnt
                print(f"  services to unlink: {cnt}")

                if args.apply:
                    new_kd = {k: v for k, v in (kd or {}).items() if k not in bad}
                    await session.execute(text(
                        "UPDATE public.service_profile "
                        "SET canonical_keys = CAST(:keys AS jsonb), "
                        "    key_descriptions = CAST(:kd AS jsonb) "
                        "WHERE id = :pid"
                    ), {
                        "keys": json.dumps(good),
                        "kd": json.dumps(new_kd),
                        "pid": str(pid),
                    })
                    await session.execute(text(
                        f"UPDATE {country}.service SET profile_id = NULL "
                        "WHERE profile_id = :pid AND canonical_key = ANY(:keys)"
                    ), {"pid": str(pid), "keys": bad})

            if args.apply:
                await session.commit()
                print(f"\nAPPLIED: removed {total_removed} bad ckey(s), unlinked {total_services_unlinked} service(s).")
            else:
                print(f"\nDRY RUN: would remove {total_removed} bad ckey(s), unlink {total_services_unlinked} service(s). Use --apply to commit.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
