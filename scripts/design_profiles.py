"""Step 5: Спроектувати UA/PL profile structure за GB-anchor mapping.

Для кожного distinct canonical_key у країні:
  1. Обчислити mean name_embedding.
  2. Знайти найближчий GB profile (cosine на profile-centroid embeddings).
  3. Якщо sim ≥ THRESHOLD і scope-сумісно → map до GB-name.
  4. Інакше → orphan.

Orphan ckeys групуємо своїм clustering (cosine ≥ 0.85, scope-aware) →
кожен cluster = новий country-specific profile (auto-named з sample name).

Виходить JSON:
  [
    {
      "name": "Манікюр",      # GB-style або orphan-derived
      "country": "ua",
      "source": "gb_anchor" | "orphan_cluster",
      "scope": null | "male" | "female" | "kids",
      "canonical_keys": ["...", "..."],
      "key_descriptions": {"<ck>": "<gen-desc>"},
      "sample_names": ["...", "..."],
      "service_count": 123
    },
    ...
  ]

Запуск:
    python -m scripts.design_profiles --country ua --threshold 0.62
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
from sqlalchemy import text

from app.infrastructure.db.session import build_engine, build_session_factory
from scripts.auto_link_missing_keys import (
    GENDER_MARKERS,
    gen_key_description,
    has_scope_marker,
    profile_scope,
    tokenize,
)
from scripts.find_ckey_clusters import DISCRIMINATORS, _violates_discriminators

OUT_DIR = Path(".logs/profile_design")


def parse_emb(raw) -> np.ndarray:
    if isinstance(raw, str):
        vec = json.loads(raw)
    else:
        vec = list(raw)
    arr = np.array(vec, dtype=np.float32)
    n = np.linalg.norm(arr)
    return arr / n if n > 0 else arr


def ckey_scope_of(ckey: str) -> str | None:
    tokens = tokenize(ckey)
    for scope in GENDER_MARKERS:
        if has_scope_marker(tokens, scope):
            return scope
    return None


async def amain() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--country", required=True, choices=["ua", "pl"])
    p.add_argument("--threshold", type=float, default=0.62,
                   help="Min cosine sim для GB-anchor match")
    p.add_argument("--orphan-cluster-threshold", type=float, default=0.82,
                   help="Min cosine для orphan clustering")
    args = p.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            # GB profiles + their service embeddings → profile centroid
            gb_rows = (await session.execute(text("""
                SELECT sp.id, sp.name, sp.canonical_keys, sp.key_descriptions,
                       AVG(s.name_embedding) AS centroid
                FROM public.service_profile sp
                LEFT JOIN gb.service s ON s.profile_id = sp.id::text
                       AND s.archive=false AND s.name_embedding IS NOT NULL
                WHERE sp.country='gb'
                GROUP BY sp.id, sp.name, sp.canonical_keys, sp.key_descriptions
                HAVING AVG(s.name_embedding) IS NOT NULL
            """))).all()

            gb_profiles = []
            for pid, name, ck, kd, cent in gb_rows:
                gb_profiles.append({
                    "id": str(pid),
                    "name": name,
                    "canonical_keys": list(ck or []),
                    "key_descriptions": dict(kd or {}),
                    "centroid": parse_emb(cent),
                    "scope": profile_scope(name),
                })
            print(f"GB anchors: {len(gb_profiles)}")

            # Country distinct ckeys
            country = args.country
            ck_rows = (await session.execute(text(f"""
                SELECT canonical_key,
                       COUNT(*) AS cnt,
                       MIN(name) AS sample,
                       AVG(name_embedding) AS centroid
                FROM {country}.service
                WHERE archive=false
                  AND canonical_key IS NOT NULL
                  AND name_embedding IS NOT NULL
                GROUP BY canonical_key
                ORDER BY canonical_key
            """))).all()

            ckeys = []
            for ck, cnt, sample, cent in ck_rows:
                ckeys.append({
                    "ckey": ck,
                    "count": cnt,
                    "sample": sample,
                    "embedding": parse_emb(cent),
                    "scope": ckey_scope_of(ck),
                })
            print(f"{country} distinct ckeys: {len(ckeys)}")

        # === Phase 1: GB anchor mapping ===
        gb_centroids = np.vstack([p["centroid"] for p in gb_profiles])
        ck_embs = np.vstack([c["embedding"] for c in ckeys])
        sims = ck_embs @ gb_centroids.T  # shape (n_ckeys, n_gb_profiles)

        mapped: dict[str, list[dict]] = defaultdict(list)  # gb_name -> [ckey rows]
        orphans: list[dict] = []

        for i, c in enumerate(ckeys):
            best_j = int(np.argmax(sims[i]))
            best_sim = float(sims[i, best_j])
            gb = gb_profiles[best_j]

            # Scope filter
            ckey_scp = c["scope"]
            gb_scp = gb["scope"]
            if gb_scp and ckey_scp and gb_scp != ckey_scp:
                # different gender → reject
                orphans.append({**c, "best_gb": None, "sim": best_sim, "reason": "scope_mismatch"})
                continue
            if ckey_scp and not gb_scp:
                # ckey gendered but GB profile neutral — only allow if GB has matching marker
                gb_ck_has_scope = any(
                    has_scope_marker(tokenize(k), ckey_scp) for k in gb["canonical_keys"]
                )
                if not gb_ck_has_scope:
                    # Better candidate? Find next GB which IS gendered match
                    order = np.argsort(-sims[i])
                    found = None
                    for j in order:
                        cand = gb_profiles[int(j)]
                        if cand["scope"] == ckey_scp:
                            found = (int(j), float(sims[i, int(j)]))
                            break
                    if found:
                        best_j, best_sim = found
                        gb = gb_profiles[best_j]
                    else:
                        orphans.append({**c, "best_gb": None, "sim": best_sim, "reason": "no_male_gb"})
                        continue

            if best_sim < args.threshold:
                orphans.append({**c, "best_gb": gb["name"], "sim": best_sim, "reason": "below_threshold"})
            else:
                mapped[gb["name"]].append({**c, "sim": best_sim})

        print(f"  → mapped to GB anchors: {sum(len(v) for v in mapped.values())} ckeys ({len(mapped)} profile names)")
        print(f"  → orphans: {len(orphans)}")

        # === Phase 2: cluster orphans → new country-specific profiles ===
        # Greedy: order orphans by service count desc; for each, if matches existing
        # orphan-cluster centroid (≥ threshold and no discriminator veto) → join,
        # else → new cluster.
        orphans_sorted = sorted(orphans, key=lambda x: -x["count"])
        orphan_clusters: list[dict] = []
        for o in orphans_sorted:
            placed = False
            for cl in orphan_clusters:
                sim = float(o["embedding"] @ cl["centroid"])
                if sim < args.orphan_cluster_threshold:
                    continue
                # Discriminator veto
                if any(_violates_discriminators(o["ckey"], m["ckey"]) for m in cl["members"]):
                    continue
                # Scope check
                if cl["scope"] and o["scope"] and cl["scope"] != o["scope"]:
                    continue
                cl["members"].append(o)
                # Recompute centroid
                vecs = np.vstack([m["embedding"] for m in cl["members"]])
                cl["centroid"] = vecs.mean(axis=0)
                cl["centroid"] /= np.linalg.norm(cl["centroid"]) or 1
                if o["scope"] and not cl["scope"]:
                    cl["scope"] = o["scope"]
                placed = True
                break
            if not placed:
                orphan_clusters.append({
                    "members": [o],
                    "centroid": o["embedding"].copy(),
                    "scope": o["scope"],
                })
        print(f"  → orphan clusters formed: {len(orphan_clusters)}")

        # === Phase 3: build profile list ===
        profiles_out: list[dict] = []

        # GB-anchored profiles
        for gb_name, items in sorted(mapped.items()):
            cks = [it["ckey"] for it in items]
            samples = [it["sample"] for it in items]
            descs = {it["ckey"]: gen_key_description(it["sample"]) for it in items}
            # scope strictly inherited from GB anchor (no fallback to item scopes
            # — інакше gendered ckey contaminates neutral profile як "Подологія")
            gb_match = next(p for p in gb_profiles if p["name"] == gb_name)
            scope = gb_match["scope"]
            profiles_out.append({
                "name": gb_name,
                "country": country,
                "source": "gb_anchor",
                "scope": scope,
                "canonical_keys": cks,
                "key_descriptions": descs,
                "sample_names": samples[:10],
                "service_count": sum(it["count"] for it in items),
            })

        # Orphan-derived
        for cl in orphan_clusters:
            members = cl["members"]
            cks = [m["ckey"] for m in members]
            samples = [m["sample"] for m in members]
            descs = {m["ckey"]: gen_key_description(m["sample"]) for m in members}
            # Auto-name: extract UA part of first sample, strip tier/length
            top_sample = max(members, key=lambda m: m["count"])["sample"]
            name = _auto_name_from_sample(top_sample)
            profiles_out.append({
                "name": name,
                "country": country,
                "source": "orphan_cluster",
                "scope": cl["scope"],
                "canonical_keys": cks,
                "key_descriptions": descs,
                "sample_names": samples[:10],
                "service_count": sum(m["count"] for m in members),
            })

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUT_DIR / f"{country}_profiles_design.json"
        out.write_text(json.dumps(profiles_out, ensure_ascii=False, indent=2))
        print(f"\nWritten: {out}")
        print(f"Total proposed profiles for {country}: {len(profiles_out)}")
        print(f"  - gb-anchored: {sum(1 for p in profiles_out if p['source']=='gb_anchor')}")
        print(f"  - orphan-derived: {sum(1 for p in profiles_out if p['source']=='orphan_cluster')}")

        print(f"\n=== Top 15 by service count ===")
        for p in sorted(profiles_out, key=lambda p: -p["service_count"])[:15]:
            scope = f"[{p['scope']}]" if p['scope'] else ""
            print(f"  [{p['source'][:3]}] {scope:8s} {p['name'][:35]:35s} {p['service_count']:4d} svc, {len(p['canonical_keys']):3d} ckeys")
    finally:
        await engine.dispose()


def _auto_name_from_sample(sample: str) -> str:
    """Extract UA portion of CRM name + clean for profile name."""
    if not sample:
        return "Інше"
    # CRM format: extract UA part
    parts = [p.strip() for p in sample.split("/")]
    ua_part = next((p[3:].strip() for p in parts if p.startswith("UA ")), parts[0] if parts else sample)
    # Strip trailing tier
    ua_part = re.sub(r"\s*(?:МАЙСТЕР|ТОП|АРТ|БАРБЕР|МАСТЕР)\s*$", "", ua_part, flags=re.IGNORECASE).strip()
    # Strip trailing "1 довжина" etc
    ua_part = re.sub(r"\s*\d+\s*довжин\w*", "", ua_part, flags=re.IGNORECASE).strip()
    ua_part = re.sub(r"\s*\(.*?\)\s*$", "", ua_part).strip()
    return ua_part[:80] or "Інше"


if __name__ == "__main__":
    asyncio.run(amain())
