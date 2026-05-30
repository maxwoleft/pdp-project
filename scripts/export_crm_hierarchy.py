"""Витягує CRM-таксономію (parent_category, category) → canonical_key+brand+service_name.

Output: .logs/crm_hierarchy.json — для нового категоризатора v3.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from sqlalchemy import text

from app.infrastructure.db.session import build_engine, build_session_factory

COUNTRIES = ("ua", "pl", "gb")
OUTPUT = Path(__file__).parent.parent / ".logs" / "crm_hierarchy.json"


# Нормалізатор багатомовних назв CRM
_LANG_PREFIX_RE = re.compile(
    r"^(?:EN|RU|RUS|UA|PL)\s+(.+?)\s*(?=$|/)",
    re.IGNORECASE
)
_LANG_BLOCK_RE = re.compile(
    r"(?:EN|RU|RUS|UA|PL)\s+([^/]+?)(?:\s*/|$)",
    re.IGNORECASE
)


def normalize_crm_name(name: str | None) -> str:
    """'EN HAIR / UA Волосся / RUS Волосы' → 'Волосся'.
    'Лікування волося' → 'Лікування волосся' (typo fix).
    """
    if not name:
        return ""
    n = name.strip()
    # Спроба витягти UA-частину
    matches = _LANG_BLOCK_RE.findall(n)
    if matches:
        # Шукаємо UA блок
        parts = re.findall(r"(EN|RU|RUS|UA|PL)\s+([^/]+?)(?:\s*/|$)", n, re.IGNORECASE)
        for tag, content in parts:
            if tag.upper() == "UA":
                return _typo_fix(content.strip())
        # Якщо немає UA — беремо перший
        return _typo_fix(matches[0].strip())
    return _typo_fix(n)


def _typo_fix(s: str) -> str:
    """Часті опечатки в CRM-назвах."""
    s = s.replace("Лікування волося", "Лікування волосся")
    s = s.replace("Нарощення", "Нарощування")
    s = re.sub(r"\s+", " ", s).strip()
    # Capitalize first letter, keep rest
    return s


async def amain() -> None:
    engine = build_engine()
    factory = build_session_factory(engine)

    services_data: list[dict] = []
    try:
        async with factory() as session:
            for c in COUNTRIES:
                rows = await session.execute(text(f"""
                    SELECT
                        s.id AS service_id,
                        s.name AS service_name,
                        s.canonical_key,
                        s.brand,
                        s.salon_id,
                        cat.name AS category_name,
                        pcat.name AS parent_category_name
                    FROM {c}.service s
                    LEFT JOIN {c}.category cat ON cat.id = s.category_id
                    LEFT JOIN {c}.category pcat ON pcat.id = cat.parent_id
                    WHERE s.archive = false
                """))
                for r in rows.all():
                    services_data.append({
                        "country": c,
                        "service_id": r.service_id,
                        "service_name": r.service_name,
                        "canonical_key": r.canonical_key,
                        "brand": r.brand,
                        "salon_id": r.salon_id,
                        "category_raw": r.category_name,
                        "parent_raw": r.parent_category_name,
                        "category": normalize_crm_name(r.category_name),
                        "parent": normalize_crm_name(r.parent_category_name) or "Без категорії",
                    })
    finally:
        await engine.dispose()

    OUTPUT.write_text(json.dumps(services_data, ensure_ascii=False, indent=2))

    # Підсумок
    by_pair: dict[tuple[str, str], dict] = {}
    for s in services_data:
        key = (s["parent"], s["category"])
        if key not in by_pair:
            by_pair[key] = {"parent": s["parent"], "category": s["category"], "services": 0, "brands": set(), "canonical_keys": set()}
        by_pair[key]["services"] += 1
        if s["brand"]:
            by_pair[key]["brands"].add(s["brand"])
        if s["canonical_key"]:
            by_pair[key]["canonical_keys"].add(s["canonical_key"])

    summary = sorted(
        [{"parent": p, "category": c, "services": v["services"],
          "unique_keys": len(v["canonical_keys"]), "brands": sorted(list(v["brands"]))}
         for (p, c), v in by_pair.items()],
        key=lambda x: -x["services"]
    )

    summary_path = OUTPUT.parent / "crm_hierarchy_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    print(f"Services exported: {len(services_data)}")
    print(f"Unique (parent, category) pairs: {len(summary)}")
    print(f"\nTop 30:")
    for s in summary[:30]:
        brands = f" brands={s['brands']}" if s['brands'] else ""
        print(f"  {s['parent']:20s} → {s['category']:50s} {s['services']:5d}svc {s['unique_keys']:4d}keys{brands}")


if __name__ == "__main__":
    asyncio.run(amain())
