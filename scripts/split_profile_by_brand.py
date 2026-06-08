"""Розщеплює profile на brand-окремі profiles.

Для profile P:
  1. Знайти distinct (ckey, brand) пари у services із цього профілю.
  2. Brand визначається з service.brand АБО fallback name-pattern detection
     (для brands яких немає у canonical_dicts.BRANDS — Color&Gloss, Alfaparf...).
  3. Для кожного non-null brand → створити "P <Brand>" (або update existing).
  4. Original profile зберігає ckeys без бренду + ckeys shared.

Запуск:
    python -m scripts.split_profile_by_brand --country ua --profile "Фарбування" --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import uuid
from collections import defaultdict

from sqlalchemy import text

from app.infrastructure.db.session import build_engine, build_session_factory
from scripts.auto_link_missing_keys import gen_key_description


BRAND_TITLE: dict[str, str] = {
    "balmain": "Balmain",
    "brae": "Brae",
    "brae bond angel": "Brae Bond Angel",
    "brae power dose": "Brae Power Dose",
    "cronna": "Cronna",
    "davines": "Davines",
    "dmk": "DMK",
    "forlled": "Forllé'd",
    "ibx": "IBX",
    "inkarami": "Inkarami",
    "inoa": "Inoa",
    "keune": "Keune",
    "la biosthetique": "La Biosthetique",
    "lebel": "Lebel",
    "loreal": "L'Oreal",
    "milbon": "Milbon",
    "nashi": "Nashi",
    "olaplex": "Olaplex",
    "oribe": "Oribe",
    "redken": "Redken",
    "revival": "Revival",
    "schwarzkopf": "Schwarzkopf",
    "tokio inkarami": "Tokio Inkarami",
    "wella": "Wella",
    "dr.sorbie": "Dr.Sorbie",
    "biologique recherche": "Biologique Recherche",
    "hadat cosmetics": "Hadat Cosmetics",
    "icoone": "Icoone",
    "aquapure": "AquaPure",
    "juvederm": "Juvederm",
    "belotero": "Belotero",
    "stylage": "Stylage",
    "teosyal": "Teosyal",
    "radiesse": "Radiesse",
    "aliaxin": "Aliaxin",
    "dermaheal": "Dermaheal",
}


def _brand_display(brand: str) -> str:
    return BRAND_TITLE.get(brand.lower(), brand.title())


# Brand patterns у service.name коли service.brand NULL (не в canonical_dicts.BRANDS).
# Виявляються лише в межах split-скрипту — не torkає sync/regen.
_NAME_BRAND_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Color&Gloss", re.compile(r"\bcolor\s*&\s*gloss\b", re.IGNORECASE)),
    ("Alfaparf", re.compile(r"\balfaparf\b", re.IGNORECASE)),
    ("Tint&Tone", re.compile(r"\btint\s*&\s*tone\b", re.IGNORECASE)),
    ("Shine&Tone", re.compile(r"\bshine\s*&\s*tone\b", re.IGNORECASE)),
    ("Yellow", re.compile(r"\byellow\b", re.IGNORECASE)),
    ("Goldwell", re.compile(r"\bgoldwell\b", re.IGNORECASE)),
    ("Vibrance", re.compile(r"\bvibrance\b", re.IGNORECASE)),
    ("Спецблонд", re.compile(r"\bспецблонд\b|\bspetsblond\b", re.IGNORECASE)),
    ("SimSens", re.compile(r"\bsimsens\b|\bsim\s*sens\b", re.IGNORECASE)),
    ("Tempting", re.compile(r"\btempting\b", re.IGNORECASE)),
    ("AMETHYSTE", re.compile(r"\bamethyste\b", re.IGNORECASE)),
    ("Lebel", re.compile(r"\blebel\b", re.IGNORECASE)),
]


def _detect_brand_from_name(name: str) -> str:
    if not name:
        return ""
    for label, pat in _NAME_BRAND_PATTERNS:
        if pat.search(name):
            return label
    return ""


async def amain() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--country", required=True, choices=["ua", "pl", "gb"])
    ap.add_argument("--profile", required=True, help="Назва profile до split")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            p = (await session.execute(text(
                "SELECT id, name, canonical_keys, key_descriptions "
                "FROM public.service_profile "
                "WHERE country=:c AND LOWER(name)=LOWER(:n)"
            ), {"c": args.country, "n": args.profile})).first()
            if not p:
                print(f"ERROR: profile '{args.profile}' not found in {args.country}")
                return
            pid, pname, p_ckeys, p_descs = p
            p_ckeys = list(p_ckeys or [])
            p_descs = dict(p_descs or {})

            # Per-service: ckey + brand-column + name → derived brand
            svc_rows = (await session.execute(text(f"""
                SELECT s.id, s.canonical_key, COALESCE(s.brand,'') AS brand, s.name
                FROM {args.country}.service s
                WHERE s.archive=false
                  AND s.canonical_key = ANY(:keys)
            """), {"keys": p_ckeys})).all()

            # Derive brand per service: prefer service.brand, fallback to name match
            agg: dict[tuple[str, str], dict] = {}
            for sid, ck, brand, name in svc_rows:
                derived = brand or _detect_brand_from_name(name or "")
                key = (ck, derived)
                if key not in agg:
                    agg[key] = {"ckey": ck, "cnt": 0, "sample": name}
                agg[key]["cnt"] += 1
                # Keep shortest sample as representative
                if name and len(name) < len(agg[key]["sample"] or ""):
                    agg[key]["sample"] = name

            by_brand: dict[str, list[dict]] = defaultdict(list)
            for (ck, brand), data in agg.items():
                by_brand[brand].append(data)

            no_brand_ckeys = {it["ckey"] for it in by_brand.get("", [])}
            brand_only_ckeys = {it["ckey"] for b, items in by_brand.items() if b
                                for it in items}
            shared = no_brand_ckeys & brand_only_ckeys
            print(f"Profile '{pname}' has {len(p_ckeys)} ckeys.")
            print(f"  - no-brand only: {len(no_brand_ckeys - brand_only_ckeys)}")
            print(f"  - branded only:  {len(brand_only_ckeys - no_brand_ckeys)}")
            print(f"  - shared (in both no-brand AND branded services): {len(shared)}")
            print()
            print(f"Brands detected:")
            for brand, items in sorted(by_brand.items()):
                if not brand:
                    continue
                display = _brand_display(brand)
                total = sum(it["cnt"] for it in items)
                print(f"  → '{pname} {display}': {len(items)} ckeys, {total} svc")

            if not args.apply:
                print("\nDRY RUN. Use --apply to commit.")
                return

            # Create or update brand profiles
            for brand, items in sorted(by_brand.items()):
                if not brand:
                    continue
                display = _brand_display(brand)
                new_name = f"{pname} {display}"
                new_ckeys = sorted({it["ckey"] for it in items})
                new_descs = {it["ckey"]: gen_key_description(it["sample"]) for it in items}

                # Existing same-name profile?
                existing = (await session.execute(text(
                    "SELECT id, canonical_keys, key_descriptions "
                    "FROM public.service_profile "
                    "WHERE country=:c AND LOWER(name)=LOWER(:n)"
                ), {"c": args.country, "n": new_name})).first()

                if existing:
                    ex_id, ex_ckeys, ex_descs = existing
                    merged_ckeys = sorted(set(ex_ckeys or []) | set(new_ckeys))
                    merged_descs = {**new_descs, **dict(ex_descs or {})}  # existing wins
                    await session.execute(text("""
                        UPDATE public.service_profile
                        SET canonical_keys = CAST(:ck AS jsonb),
                            key_descriptions = CAST(:kd AS jsonb)
                        WHERE id = :id
                    """), {
                        "ck": json.dumps(merged_ckeys),
                        "kd": json.dumps(merged_descs),
                        "id": str(ex_id),
                    })
                    print(f"  ⟳ updated '{new_name}' → {len(merged_ckeys)} ckeys")
                else:
                    # Pick primary canonical_key — sortuje первый. Ensure unique per country.
                    primary = new_ckeys[0] if new_ckeys else f"profile_{uuid.uuid4().hex[:8]}"
                    suffix = 0
                    candidate = primary
                    while True:
                        conflict = (await session.execute(text(
                            "SELECT 1 FROM public.service_profile "
                            "WHERE country=:c AND canonical_key=:k"
                        ), {"c": args.country, "k": candidate})).scalar()
                        if not conflict:
                            break
                        suffix += 1
                        candidate = f"{primary[:90]}_{suffix}"
                    await session.execute(text("""
                        INSERT INTO public.service_profile
                        (id, canonical_key, name, country, salon_ids, cities,
                         canonical_keys, key_descriptions, keywords_by_lang,
                         enabled, default_language, current_version,
                         created_at, updated_at, created_by, updated_by)
                        VALUES (:id, :ck, :name, :country, '[]'::jsonb, '[]'::jsonb,
                                CAST(:ckl AS jsonb),
                                CAST(:descs AS jsonb),
                                '{}'::jsonb,
                                true, 'uk', 1,
                                NOW(), NOW(), 'split_brand', 'split_brand')
                    """), {
                        "id": str(uuid.uuid4()),
                        "ck": candidate,
                        "name": new_name[:255],
                        "country": args.country,
                        "ckl": json.dumps(new_ckeys),
                        "descs": json.dumps(new_descs),
                    })
                    print(f"  + created '{new_name}' → {len(new_ckeys)} ckeys")

            # Update original profile: keep only no-brand ckeys
            # (видаляємо з нього лише ckeys що ВИКЛЮЧНО branded, тобто немає
            #  services без бренда. Якщо ckey і там і там — shared, лишаємо.)
            branded_only = brand_only_ckeys - no_brand_ckeys
            new_p_ckeys = sorted(set(p_ckeys) - branded_only)
            new_p_descs = {k: v for k, v in p_descs.items() if k in new_p_ckeys}
            await session.execute(text("""
                UPDATE public.service_profile
                SET canonical_keys = CAST(:ck AS jsonb),
                    key_descriptions = CAST(:kd AS jsonb)
                WHERE id = :id
            """), {
                "ck": json.dumps(new_p_ckeys),
                "kd": json.dumps(new_p_descs),
                "id": str(pid),
            })
            await session.commit()
            print(f"\n  ⟳ original '{pname}' → {len(new_p_ckeys)} ckeys (removed {len(branded_only)} branded-only)")
            print("\nAPPLIED.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
