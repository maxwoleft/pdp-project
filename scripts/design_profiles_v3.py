"""Step 5 v3: Pure category-based.

  Profile := leaf CRM category. Усі services категорії → той profile.
  canonical_keys[] = distinct ckeys у services.
  Gender override: services з male marker → "Чоловічі послуги" bucket.
  Skip CRM-шум categories (Offers of the month, Archive, тощо).

Запуск:
    python -m scripts.design_profiles_v3 --country ua
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import defaultdict
from pathlib import Path

from sqlalchemy import text

from app.domain.services.canonical_key import extract_uk_part_from_crm
from app.infrastructure.db.session import build_engine, build_session_factory
from scripts.auto_link_missing_keys import gen_key_description, profile_scope

OUT_DIR = Path(".logs/profile_design")

_SKIP_CATEGORY_RE = re.compile(
    r"offers of the month|пропозиції місяця|предложения месяца|"
    r"архів|archive|webinar|вебінар",
    re.IGNORECASE,
)


def _clean_category_name(raw: str) -> str:
    ua = extract_uk_part_from_crm(raw or "")
    return re.sub(r"\s+", " ", ua).strip()


def _service_has_male(name: str) -> bool:
    if not name:
        return False
    return bool(re.search(r"чолов|мужск|\bmen\b|\bmale\b", name.lower()))


async def amain() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--country", required=True, choices=["ua", "pl"])
    args = p.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)
    country = args.country

    try:
        async with factory() as session:
            rows = (await session.execute(text(f"""
                SELECT s.id, s.name, s.canonical_key, c.name AS cat_name
                FROM {country}.service s
                JOIN {country}.category c ON c.id = s.category_id
                WHERE s.archive=false
                  AND s.canonical_key IS NOT NULL
                  AND c.archive=false
            """))).all()
            print(f"[{country}] services: {len(rows)}")

        # Group by category (cleaned)
        by_cat: dict[str, list[dict]] = defaultdict(list)
        male_bucket: list[dict] = []

        for sid, sname, ck, cat_name in rows:
            clean_cat = _clean_category_name(cat_name)
            if _SKIP_CATEGORY_RE.search(clean_cat):
                continue
            ua_name = extract_uk_part_from_crm(sname or "")
            svc = {"id": sid, "raw_name": sname, "name": ua_name, "ckey": ck}
            if _service_has_male(ua_name):
                male_bucket.append(svc)
            else:
                by_cat[clean_cat].append(svc)

        profiles_out = []
        for cat_name, svcs in sorted(by_cat.items()):
            samples_by_ck: dict[str, str] = {}
            for s in svcs:
                samples_by_ck.setdefault(s["ckey"], s["raw_name"])
            descs = {ck: gen_key_description(sample) for ck, sample in samples_by_ck.items()}
            profiles_out.append({
                "name": cat_name,
                "country": country,
                "scope": profile_scope(cat_name),
                "category_origin": cat_name,
                "canonical_keys": sorted(samples_by_ck.keys()),
                "key_descriptions": descs,
                "sample_names": list(samples_by_ck.values())[:10],
                "service_count": len(svcs),
            })

        # Чоловічі послуги bucket
        if male_bucket:
            samples_by_ck: dict[str, str] = {}
            for s in male_bucket:
                samples_by_ck.setdefault(s["ckey"], s["raw_name"])
            descs = {ck: gen_key_description(sample) for ck, sample in samples_by_ck.items()}
            profiles_out.append({
                "name": "Чоловічі послуги",
                "country": country,
                "scope": "male",
                "category_origin": "<gender bucket>",
                "canonical_keys": sorted(samples_by_ck.keys()),
                "key_descriptions": descs,
                "sample_names": list(samples_by_ck.values())[:10],
                "service_count": len(male_bucket),
            })

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUT_DIR / f"{country}_profiles_v3.json"
        out.write_text(json.dumps(profiles_out, ensure_ascii=False, indent=2))

        sizes = sorted(len(p["canonical_keys"]) for p in profiles_out)
        total_svc = sum(p["service_count"] for p in profiles_out)
        print(f"\n=== Summary [{country}] v3 (pure category) ===")
        print(f"  Total profiles: {len(profiles_out)}")
        print(f"  Total services covered: {total_svc}")
        print(f"  ckey distribution: 1: {sum(1 for s in sizes if s==1)} | "
              f"2-3: {sum(1 for s in sizes if 2<=s<=3)} | "
              f"4-10: {sum(1 for s in sizes if 4<=s<=10)} | "
              f"11-30: {sum(1 for s in sizes if 11<=s<=30)} | "
              f"31+: {sum(1 for s in sizes if s>30)}")
        print(f"  Written: {out}")

        print(f"\n=== Top 25 by service count ===")
        for p in sorted(profiles_out, key=lambda x: -x["service_count"])[:25]:
            sc = f"[{p['scope']}]" if p["scope"] else ""
            print(f"  {sc:9s} {p['name'][:50]:50s} {p['service_count']:4d} svc  {len(p['canonical_keys']):3d} ckeys")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
