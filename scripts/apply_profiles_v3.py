"""Step 6: Apply v3 profile design до public.service_profile.

  1. Load JSON design.
  2. Normalize names (strip trailing parens, fix UA typos).
  3. Merge profiles з ідентичними normalized names.
  4. INSERT — створює profile rows БЕЗ linkування сервісів (profile_id залишається NULL).
     User перегляне у адмінці, потім окремий step linkує.

Запуск:
    python -m scripts.apply_profiles_v3 --country ua --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import uuid
from collections import defaultdict
from pathlib import Path

from sqlalchemy import text

from app.infrastructure.db.session import build_engine, build_session_factory

IN_DIR = Path(".logs/profile_design")

# UA name typos з CRM-категорій
_NAME_TYPOS: list[tuple[str, str]] = [
    (r"\bволося\b", "волосся"),
    (r"\bпеликюр\b", "педикюр"),
    (r"\bманікур\b", "манікюр"),
    (r"\bепиляція\b", "епіляція"),
]


def _normalize_name(name: str) -> str:
    n = (name or "").strip()
    # Strip trailing parens content (е.g., "Лазерна епіляція (жінки)" → "Лазерна епіляція")
    n = re.sub(r"\s*\([^)]*\)\s*$", "", n)
    # Apply UA typo fixes
    for pat, repl in _NAME_TYPOS:
        n = re.sub(pat, repl, n, flags=re.IGNORECASE)
    n = re.sub(r"\s{2,}", " ", n).strip()
    return n


async def amain() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--country", required=True, choices=["ua", "pl"])
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()

    in_file = IN_DIR / f"{args.country}_profiles_v3.json"
    designs = json.loads(in_file.read_text())
    print(f"[{args.country}] loaded {len(designs)} raw profiles from {in_file}")

    # Normalize + group
    by_name: dict[str, list[dict]] = defaultdict(list)
    for d in designs:
        norm = _normalize_name(d["name"])
        if not norm:
            continue
        by_name[norm].append(d)

    print(f"[{args.country}] after normalize: {len(by_name)} unique names")

    merged: list[dict] = []
    for name, items in sorted(by_name.items()):
        all_ckeys: set[str] = set()
        all_descs: dict[str, str] = {}
        all_samples: list[str] = []
        all_svc = 0
        scopes = {it.get("scope") for it in items if it.get("scope")}
        origins = sorted({it.get("category_origin") for it in items if it.get("category_origin")})
        for it in items:
            all_ckeys.update(it.get("canonical_keys") or [])
            all_descs.update(it.get("key_descriptions") or {})
            all_samples.extend(it.get("sample_names") or [])
            all_svc += it.get("service_count") or 0
        ckeys_sorted = sorted(all_ckeys)
        primary = ckeys_sorted[0] if ckeys_sorted else _slugify_name(name)
        merged.append({
            "name": name,
            "country": args.country,
            "scope": next(iter(scopes)) if len(scopes) == 1 else None,
            "canonical_key": primary,
            "canonical_keys": ckeys_sorted,
            "key_descriptions": all_descs,
            "sample_names": all_samples[:10],
            "service_count": all_svc,
            "origins": origins,
        })

    # Print summary of merges where >1 source
    multi_source = [m for m in merged if len(m["origins"]) > 1]
    print(f"[{args.country}] merged from multiple origins: {len(multi_source)}")
    for m in multi_source[:20]:
        print(f"  ⤬ '{m['name']}' ← {m['origins']}")

    print(f"\n[{args.country}] total merged profiles: {len(merged)}")

    if not args.apply:
        print("\nDRY RUN. Use --apply.")
        return

    engine = build_engine()
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            inserted = 0
            for p in merged:
                pid = str(uuid.uuid4())
                # Handle primary canonical_key uniqueness conflict by suffix.
                primary = p["canonical_key"][:100]
                suffix = 0
                while True:
                    exists = (await session.execute(text(
                        "SELECT 1 FROM public.service_profile "
                        "WHERE country=:c AND canonical_key=:k"
                    ), {"c": p["country"], "k": primary})).scalar()
                    if not exists:
                        break
                    suffix += 1
                    primary = (p["canonical_key"][:90] + f"_{suffix}")[:100]
                await session.execute(text("""
                    INSERT INTO public.service_profile
                    (id, canonical_key, name, country, salon_ids, cities,
                     canonical_keys, key_descriptions, keywords_by_lang,
                     enabled, default_language, current_version,
                     created_at, updated_at, created_by, updated_by)
                    VALUES (:id, :ck, :name, :country, '[]'::jsonb, '[]'::jsonb,
                            CAST(:ck_list AS jsonb),
                            CAST(:descs AS jsonb),
                            '{}'::jsonb,
                            true, 'uk', 1,
                            NOW(), NOW(), 'profile_rebuild_v3', 'profile_rebuild_v3')
                """), {
                    "id": pid,
                    "ck": primary,
                    "name": p["name"][:255],
                    "country": p["country"],
                    "ck_list": json.dumps(p["canonical_keys"]),
                    "descs": json.dumps(p["key_descriptions"]),
                })
                inserted += 1
            await session.commit()
            print(f"\n[{args.country}] INSERTED {inserted} profiles.")
            print(f"NOTE: services.profile_id ще NULL — користувач переглядає, потім окремо linkує.")
    finally:
        await engine.dispose()


def _slugify_name(name: str) -> str:
    """Fallback primary ckey з імені profile."""
    from app.domain.services.canonical_key import _make_slug
    return _make_slug(name, sort_tokens=False) or "profile"


if __name__ == "__main__":
    asyncio.run(amain())
