"""Cleanup всіх service_profile після оновлення алгоритму canonical_key.

Логіка для кожного profile:
  1. expected_key = extract_attributes(profile.name).base_name
  2. has_svc_with_current = чи існує service з canonical_key == profile.canonical_key
  3. has_svc_with_expected = чи існує service з canonical_key == expected_key

  - Якщо has_svc_with_current → лишаємо як є (профіль валідний)
  - Якщо not has_svc_with_current AND has_svc_with_expected → UPDATE profile.canonical_key = expected_key
  - Якщо not has_svc_with_current AND not has_svc_with_expected → DELETE profile (orphan)

Двофазне UPDATE (placeholder) — для уникнення UNIQUE-колізій між профілями.

Колізії при UPDATE (двох профілів на один target) — детектуються і:
  - Type A (ідентичні name): merge, як у merge_duplicate_profiles
  - Type B (різні name): keep oldest, delete новіший

Запуск:
  python -m scripts.cleanup_profiles                # dry run
  python -m scripts.cleanup_profiles --apply
  python -m scripts.cleanup_profiles --apply --keep-orphans  # не видаляти orphan'и, тільки rematch
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from sqlalchemy import delete, select, text, update

from app.domain.services.canonical_key import extract_attributes, make_canonical_key
from app.infrastructure.db.models.profile import ServiceProfile
from app.infrastructure.db.session import build_engine, build_session_factory

COUNTRIES = ("ua", "pl", "gb")


async def get_all_service_keys(session) -> set[str]:
    """Усі canonical_keys що існують в актуальних послугах (ua/pl/gb)."""
    keys: set[str] = set()
    for c in COUNTRIES:
        rows = await session.execute(text(f"""
            SELECT DISTINCT canonical_key FROM {c}.service
            WHERE canonical_key IS NOT NULL AND archive = false
        """))
        keys.update(r[0] for r in rows.all() if r[0])
    return keys


async def count_similar_services(session, expected_key: str) -> int:
    """Скільки сервісів мають canonical_key зі спільним коренем з expected_key.

    Family-level профіль не має точного матчу, але має префікс/токен-матчі.

    Стратегія:
      - Для кожного токена expected_key з ≥5 символів — substring search прийнятна
      - Для коротких токенів (≤4 chars) — token-position match (точне співпадіння токена)
      - Підраховуємо services, що мають ХОЧ ЯКИЙ матч (DISTINCT count)
    """
    if not expected_key:
        return 0

    tokens = [t for t in expected_key.split("_") if t]
    if not tokens:
        return 0

    where_clauses = []
    params: dict = {}
    for i, tok in enumerate(tokens):
        if len(tok) >= 5:
            # Substring search — використовуємо префікс перші 5 chars
            params[f"sub{i}"] = f"%{tok[:6]}%"
            where_clauses.append(f"canonical_key LIKE :sub{i}")
        else:
            # Token position match (між _ або краями)
            params[f"t{i}"] = tok
            params[f"ts{i}"] = f"{tok}_%"
            params[f"te{i}"] = f"%_{tok}"
            params[f"tm{i}"] = f"%_{tok}_%"
            where_clauses.append(
                f"(canonical_key = :t{i} OR canonical_key LIKE :ts{i} "
                f"OR canonical_key LIKE :te{i} OR canonical_key LIKE :tm{i})"
            )

    where_sql = " OR ".join(where_clauses)
    total = 0
    for c in COUNTRIES:
        row = await session.execute(
            text(f"""
                SELECT COUNT(*) FROM {c}.service
                WHERE archive = false AND ({where_sql})
            """),
            params,
        )
        total += row.scalar() or 0
    return total


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--keep-orphans", action="store_true",
                        help="Не видаляти профілі без жодного матчу (тільки UPDATE)")
    args = parser.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            service_keys = await get_all_service_keys(session)
            print(f"=== Service canonical_keys у БД: {len(service_keys)} ===")

            profiles = list((await session.execute(select(ServiceProfile))).scalars())
            print(f"=== Profiles у БД: {len(profiles)} ===\n")

            updates_pending: list[tuple[str, str, str]] = []   # (pid, old_key, new_key)
            deletions_pending: list[tuple[str, str, str]] = [] # (pid, key, name)
            family_level: list[tuple[str, str, int]] = []      # (pid, name, similar_count)
            collisions: list[tuple[str, list[str]]] = []
            keep_count = 0

            # Зібрати плани
            target_keys: dict[str, list[ServiceProfile]] = {}  # new_key → profiles що хочуть туди
            for p in profiles:
                has_current = p.canonical_key in service_keys
                if has_current:
                    keep_count += 1
                    continue

                # Сирота — спробувати rematch
                expected = None
                if p.name:
                    attrs = extract_attributes(p.name)
                    expected = make_canonical_key(attrs)
                has_expected = expected and expected in service_keys

                if has_expected:
                    target_keys.setdefault(expected, []).append(p)
                    continue

                # Family-level: ≥1 schвжий сервіс (substring/token match) — зберігаємо
                similar = await count_similar_services(session, expected) if expected else 0
                if similar >= 1:
                    family_level.append((p.id, p.name or p.canonical_key, similar))
                    continue

                deletions_pending.append((p.id, p.canonical_key, p.name or "(no name)"))

            # Розгрупувати target_keys → колізії і безпечні
            for new_key, group in target_keys.items():
                # Чи new_key вже зайнятий іншим профілем (не з цієї групи, не з orphan list)?
                already_using = next(
                    (p for p in profiles
                     if p.canonical_key == new_key and p not in group),
                    None
                )
                if already_using:
                    # Колізія з не-orphan профілем — orphan(s) залишаємо як є або видаляємо
                    names = [p.canonical_key for p in group]
                    collisions.append((new_key, names))
                    # Додаємо до deletion як "немає де матчити"
                    for p in group:
                        deletions_pending.append((p.id, p.canonical_key, p.name or "(no name)"))
                    continue

                if len(group) > 1:
                    # 2+ orphan'и хочуть один new_key — keep oldest, решта — delete
                    group_sorted = sorted(group, key=lambda x: (x.created_at or 0, x.id))
                    master = group_sorted[0]
                    updates_pending.append((master.id, master.canonical_key, new_key))
                    for p in group_sorted[1:]:
                        deletions_pending.append((p.id, p.canonical_key, p.name or "(no name)"))
                else:
                    p = group[0]
                    updates_pending.append((p.id, p.canonical_key, new_key))

            print(f"=== План ===")
            print(f"  KEEP (валідний канонічний ключ): {keep_count}")
            print(f"  UPDATE (rematch до існуючої послуги): {len(updates_pending)}")
            print(f"  FAMILY-LEVEL (зберегти як knowledge, без точного match): {len(family_level)}")
            print(f"  DELETE (true orphan, немає схожих services): {len(deletions_pending)}")
            if collisions:
                print(f"  COLLISIONS (target зайнятий іншим): {len(collisions)}")

            if family_level[:10]:
                print(f"\n  Sample FAMILY-LEVEL:")
                for pid, name, similar in family_level[:8]:
                    print(f"    '{name[:60]}' — {similar} схожих services")

            if updates_pending and updates_pending[:5]:
                print(f"\n  Sample UPDATEs:")
                for pid, old, new in updates_pending[:5]:
                    print(f"    {old!r:50s} → {new!r}")

            if deletions_pending and deletions_pending[:5]:
                print(f"\n  Sample DELETEs:")
                for pid, key, name in deletions_pending[:5]:
                    print(f"    {key!r:50s} ← '{name[:60]}'")

            if not args.apply:
                print("\n(DRY RUN — use --apply)")
                return

            # Виконання
            # 1. UPDATE phase 1 — placeholder
            for pid, _, _ in updates_pending:
                await session.execute(
                    update(ServiceProfile)
                    .where(ServiceProfile.id == pid)
                    .values(canonical_key=f"__migrating_{pid}__")
                )
            await session.flush()
            # 2. UPDATE phase 2 — real keys
            for pid, _, new_key in updates_pending:
                await session.execute(
                    update(ServiceProfile)
                    .where(ServiceProfile.id == pid)
                    .values(canonical_key=new_key)
                )
            await session.flush()

            # 3. DELETE orphans
            if not args.keep_orphans:
                for pid, _, _ in deletions_pending:
                    await session.execute(
                        delete(ServiceProfile).where(ServiceProfile.id == pid)
                    )
                await session.flush()

            await session.commit()
            print(f"\nDONE: updated={len(updates_pending)} deleted="
                  f"{0 if args.keep_orphans else len(deletions_pending)}")

    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
