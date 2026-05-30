"""Розв'язання колізій canonical_key у service_profile.

Знаходить групи profiles з однаковим новим canonical_key (computed з name),
класифікує і:
  - Type A (чисті дублікати, ідентичні name): auto-merge
  - Type B (категорійний _cat_* + основний): auto-видалення _cat_*
  - Type C (бренд-варіанти, name різні): interactive review

При мерджі:
  1. Обирає master (найбільше translations, потім options, потім oldest)
  2. Reparent дочірніх записів:
     - service_profile_translation (UNIQUE по profile_id+language)
     - service_profile_option
     - service_profile_version (UNIQUE по profile_id+version_number)
     - service_profile_variant (UNIQUE по profile_id+language+label)
     - service_profile_override (UNIQUE по profile_id+service_id+country)
  3. DELETE source profile (CASCADE доб'є те, що не вдалося перенести)
  4. UPDATE master.canonical_key → new_key (двофазно через placeholder)

Запуск:
    python -m scripts.merge_duplicate_profiles                  # dry run
    python -m scripts.merge_duplicate_profiles --apply --auto   # auto Type A/B, skip C
    python -m scripts.merge_duplicate_profiles --apply          # interactive C теж
"""
from __future__ import annotations

import argparse
import asyncio
from typing import Iterable

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.services.canonical_key import extract_attributes
from app.infrastructure.db.models.profile import (
    ServiceProfile,
    ServiceProfileOption,
    ServiceProfileTranslation,
    ServiceProfileVariant,
    ServiceProfileVersion,
)
from app.infrastructure.db.session import build_engine, build_session_factory


# ── Класифікація колізій ─────────────────────────────────────────


def classify_group(profiles: list[ServiceProfile]) -> str:
    """Type A / B / C для групи з однаковим target canonical_key."""
    names = {(p.name or "").strip() for p in profiles}
    has_cat = any(
        (p.canonical_key or "").startswith("_cat_") for p in profiles
    )
    if has_cat and len(names) >= 1:
        return "B"
    if len(names) == 1:
        return "A"
    return "C"


# ── Вибір master ────────────────────────────────────────────────


async def _profile_richness(session: AsyncSession, profile_id: str) -> tuple[int, int]:
    """(translations, options) — для вибору master."""
    t_count = (
        await session.execute(
            select(text("COUNT(*)"))
            .select_from(ServiceProfileTranslation)
            .where(ServiceProfileTranslation.profile_id == profile_id)
        )
    ).scalar() or 0
    o_count = (
        await session.execute(
            select(text("COUNT(*)"))
            .select_from(ServiceProfileOption)
            .where(ServiceProfileOption.profile_id == profile_id)
        )
    ).scalar() or 0
    return int(t_count), int(o_count)


async def pick_master(
    session: AsyncSession, profiles: list[ServiceProfile], group_type: str
) -> ServiceProfile:
    """Master = той що зберігаємо.

    Type B: master = той що НЕ _cat_*.
    Інакше: master = найбагатший по translations + options, далі oldest.
    """
    if group_type == "B":
        non_cat = [p for p in profiles if not (p.canonical_key or "").startswith("_cat_")]
        if non_cat:
            return non_cat[0]

    enriched: list[tuple[int, int, ServiceProfile]] = []
    for p in profiles:
        t, o = await _profile_richness(session, p.id)
        enriched.append((t, o, p))

    enriched.sort(
        key=lambda x: (-x[0], -x[1], x[2].created_at or 0)  # max trans, max opts, then oldest
    )
    return enriched[0][2]


# ── Перенесення дочірніх записів ────────────────────────────────


async def merge_translations(session, master_id: str, source_id: str) -> int:
    """Translations: UNIQUE(profile_id, language). Якщо master уже має ту мову —
    залишаємо master's, source's видаляємо. Якщо ні — переносимо."""
    master_langs = {
        row[0] for row in (
            await session.execute(
                select(ServiceProfileTranslation.language).where(
                    ServiceProfileTranslation.profile_id == master_id
                )
            )
        ).all()
    }
    source_trans = list(
        (
            await session.execute(
                select(ServiceProfileTranslation).where(
                    ServiceProfileTranslation.profile_id == source_id
                )
            )
        ).scalars()
    )
    moved = 0
    for tr in source_trans:
        if tr.language in master_langs:
            continue  # CASCADE delete пізніше
        await session.execute(
            update(ServiceProfileTranslation)
            .where(ServiceProfileTranslation.id == tr.id)
            .values(profile_id=master_id)
        )
        moved += 1
    await session.flush()
    return moved


async def merge_options(session, master_id: str, source_id: str) -> int:
    """Options: без UNIQUE — переносимо всі."""
    result = await session.execute(
        update(ServiceProfileOption)
        .where(ServiceProfileOption.profile_id == source_id)
        .values(profile_id=master_id)
    )
    await session.flush()
    return result.rowcount or 0


async def merge_versions(session, master_id: str, source_id: str) -> int:
    """Versions: UNIQUE(profile_id, version_number). Зсуваємо source's номери."""
    master_max = (
        await session.execute(
            select(text("COALESCE(MAX(version_number), 0)"))
            .select_from(ServiceProfileVersion)
            .where(ServiceProfileVersion.profile_id == master_id)
        )
    ).scalar() or 0

    source_versions = list(
        (
            await session.execute(
                select(ServiceProfileVersion)
                .where(ServiceProfileVersion.profile_id == source_id)
                .order_by(ServiceProfileVersion.version_number)
            )
        ).scalars()
    )
    moved = 0
    for v in source_versions:
        await session.execute(
            update(ServiceProfileVersion)
            .where(ServiceProfileVersion.id == v.id)
            .values(profile_id=master_id, version_number=master_max + moved + 1)
        )
        moved += 1
    await session.flush()
    return moved


async def merge_variants(session, master_id: str, source_id: str) -> int:
    """Variants: UNIQUE(profile_id, language, label). Конфлікти — видаляємо source's."""
    master_keys = {
        (lang, label)
        for lang, label in (
            await session.execute(
                select(ServiceProfileVariant.language, ServiceProfileVariant.label).where(
                    ServiceProfileVariant.profile_id == master_id
                )
            )
        ).all()
    }
    source_variants = list(
        (
            await session.execute(
                select(ServiceProfileVariant).where(
                    ServiceProfileVariant.profile_id == source_id
                )
            )
        ).scalars()
    )
    moved = 0
    for v in source_variants:
        if (v.language, v.label) in master_keys:
            continue
        await session.execute(
            update(ServiceProfileVariant)
            .where(ServiceProfileVariant.id == v.id)
            .values(profile_id=master_id)
        )
        moved += 1
    await session.flush()
    return moved


async def merge_one_into_master(
    session, master: ServiceProfile, source: ServiceProfile
) -> dict:
    """Переносить translations/options/versions/variants з source у master,
    потім DELETE source. Повертає stats."""
    stats = {
        "translations": await merge_translations(session, master.id, source.id),
        "options": await merge_options(session, master.id, source.id),
        "versions": await merge_versions(session, master.id, source.id),
        "variants": await merge_variants(session, master.id, source.id),
    }
    # DELETE source — CASCADE видалить що залишилось
    await session.execute(
        delete(ServiceProfile).where(ServiceProfile.id == source.id)
    )
    await session.flush()
    return stats


# ── Interactive prompt для Type C ───────────────────────────────


async def prompt_type_c(
    session, profiles: list[ServiceProfile], new_key: str
) -> tuple[str, ServiceProfile | None]:
    """Повертає ('merge', master) | ('keep_separate', None) | ('skip', None)."""
    print(f"\n  Type C — потребує рішення (target new_key={new_key!r}):")
    for p in profiles:
        t, o = await _profile_richness(session, p.id)
        print(
            f"    [{p.id[:8]}] name={p.name!r}  current_key={p.canonical_key!r}  "
            f"trans={t} opts={o}"
        )
    while True:
        ans = input("    [M]erge / [K]eep separate / [S]kip: ").strip().lower()
        if ans in ("m", "merge"):
            master = await pick_master(session, profiles, "C")
            print(f"    → merge into master {master.id[:8]} ({master.name!r})")
            return "merge", master
        if ans in ("k", "keep"):
            print("    → лишаємо як є, canonical_key не оновлюємо")
            return "keep_separate", None
        if ans in ("s", "skip"):
            return "skip", None


# ── Two-phase canonical_key update ──────────────────────────────


async def update_canonical_keys(
    session, planned_updates: list[tuple[str, str]]
) -> None:
    """Двофазно оновлює canonical_key через тимчасовий placeholder."""
    if not planned_updates:
        return
    for pid, _ in planned_updates:
        await session.execute(
            update(ServiceProfile)
            .where(ServiceProfile.id == pid)
            .values(canonical_key=f"__migrating_{pid}__")
        )
    await session.flush()
    for pid, new_key in planned_updates:
        await session.execute(
            update(ServiceProfile)
            .where(ServiceProfile.id == pid)
            .values(canonical_key=new_key)
        )
    await session.flush()


# ── Main ─────────────────────────────────────────────────────────


def find_collisions(
    profiles: Iterable[ServiceProfile],
) -> dict[str, list[ServiceProfile]]:
    by_new_key: dict[str, list[ServiceProfile]] = {}
    for p in profiles:
        if not p.name:
            continue
        new_key = extract_attributes(p.name)["base_name"]
        if not new_key:
            continue
        by_new_key.setdefault(new_key, []).append(p)
    return {k: g for k, g in by_new_key.items() if len(g) > 1}


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Реально мерджити")
    parser.add_argument("--auto", action="store_true",
                        help="Skip Type C (interactive). Auto-merge Type A/B only.")
    parser.add_argument("--merge-all", action="store_true",
                        help="Auto-merge ВСІ типи включно з C (без prompts). "
                             "Master = найбагатший по translations + options + oldest.")
    args = parser.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            profiles = list((await session.execute(select(ServiceProfile))).scalars())
            collisions = find_collisions(profiles)

            print(f"=== Знайдено {len(collisions)} колізійних груп ===\n")

            counts = {"A": 0, "B": 0, "C": 0}
            for grp in collisions.values():
                counts[classify_group(grp)] += 1
            print(f"Type A (чисті дублікати): {counts['A']}")
            print(f"Type B (категорійний+основний): {counts['B']}")
            print(f"Type C (бренд/concept): {counts['C']}")

            if not args.apply:
                print("\n(DRY RUN — use --apply)")
                return

            planned_canonical_updates: list[tuple[str, str]] = []
            merged_total = 0

            for new_key, group in sorted(collisions.items()):
                gtype = classify_group(group)
                print(f"\n[{gtype}] {new_key}  ({len(group)} profiles)")

                if gtype == "C" and args.auto:
                    print("  → skip (--auto)")
                    continue

                if gtype == "C" and not args.merge_all:
                    decision, master = await prompt_type_c(session, group, new_key)
                    if decision != "merge":
                        continue
                else:
                    master = await pick_master(session, group, gtype)
                    print(
                        f"  master = {master.id[:8]} ({master.canonical_key!r})"
                    )

                # Перенести і видалити інших
                for p in group:
                    if p.id == master.id:
                        continue
                    stats = await merge_one_into_master(session, master, p)
                    print(
                        f"  ← merged {p.id[:8]} ({p.canonical_key!r}): "
                        f"trans={stats['translations']} opts={stats['options']} "
                        f"vers={stats['versions']} vars={stats['variants']}"
                    )
                    merged_total += 1

                # Запланувати UPDATE master.canonical_key → new_key
                if master.canonical_key != new_key:
                    planned_canonical_updates.append((master.id, new_key))

            print(f"\n=== Two-phase canonical_key UPDATE: {len(planned_canonical_updates)} masters ===")
            await update_canonical_keys(session, planned_canonical_updates)
            await session.commit()

            print(f"\nDONE: merged {merged_total} duplicates into masters, "
                  f"updated {len(planned_canonical_updates)} canonical_keys")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
