"""Step 5 v2: Category-based design з GB-refinement.

Підхід:
  Base: profile := leaf CRM category. Свої services → той profile.
  Refine 1 (split): усередині category, якщо ckey embeddings формують
    distinct subclusters → split на N profiles per category (GB principle:
    Airtouch ≠ Balayage хоч обидва "Техніки висвітлення").
  Refine 2 (gender): services з male marker у name → reassign до
    "Чоловічі послуги" (GB principle: one male bucket).

Запуск:
    python -m scripts.design_profiles_v2 --country ua --split-threshold 0.72
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

from app.domain.services.canonical_key import extract_uk_part_from_crm
from app.infrastructure.db.session import build_engine, build_session_factory
from scripts.auto_link_missing_keys import (
    GENDER_MARKERS,
    gen_key_description,
    has_scope_marker,
    profile_scope,
    tokenize,
)
from scripts.find_ckey_clusters import _violates_discriminators

OUT_DIR = Path(".logs/profile_design")

# Категорії-шум — фактично не категорії, а CRM-маркери, скіпаємо
_SKIP_CATEGORY_RE = re.compile(
    r"offers of the month|пропозиції місяця|предложения месяца|"
    r"архів|archive",
    re.IGNORECASE,
)


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


def _clean_category_name(raw: str) -> str:
    """CRM 'EN ... / UA ... / RUS ...' → UA частина."""
    ua = extract_uk_part_from_crm(raw or "")
    ua = re.sub(r"\s+", " ", ua).strip()
    return ua


def _greedy_subcluster(
    ckeys: list[str],
    embs: list[np.ndarray],
    threshold: float,
) -> list[list[int]]:
    """Greedy: sortuj за popularity (через embs assumes pre-sorted ззовні),
    призначай до існуючого cluster якщо sim ≥ threshold + no discriminator
    veto, інакше новий cluster."""
    clusters: list[dict] = []  # [{centroid, members:[indices]}]
    for i, e in enumerate(embs):
        placed = False
        for cl in clusters:
            sim = float(e @ cl["centroid"])
            if sim < threshold:
                continue
            if any(_violates_discriminators(ckeys[i], ckeys[m]) for m in cl["members"]):
                continue
            cl["members"].append(i)
            vecs = np.vstack([embs[m] for m in cl["members"]])
            cl["centroid"] = vecs.mean(axis=0)
            n = np.linalg.norm(cl["centroid"])
            if n > 0:
                cl["centroid"] /= n
            placed = True
            break
        if not placed:
            clusters.append({"centroid": e.copy(), "members": [i]})
    return [cl["members"] for cl in clusters]


def _service_has_gender(name: str, scope: str) -> bool:
    """Check service name (already extracted UA part)."""
    if not name:
        return False
    n = name.lower()
    if scope == "male":
        return bool(re.search(r"чолов|мужск|\bmen\b|\bmale\b", n))
    if scope == "female":
        return bool(re.search(r"жіноч|\bжен\b|\bwomen\b", n))
    if scope == "kids":
        return bool(re.search(r"дитяч|\bкід|\bkids?\b|\bchild", n))
    return False


async def amain() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--country", required=True, choices=["ua", "pl"])
    p.add_argument("--split-threshold", type=float, default=0.72,
                   help="Min cosine для intra-category cluster merge.")
    p.add_argument("--min-cluster-svc", type=int, default=1)
    args = p.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)
    country = args.country

    try:
        async with factory() as session:
            rows = (await session.execute(text(f"""
                SELECT s.id, s.name, s.canonical_key, s.name_embedding,
                       c.id AS cat_id, c.name AS cat_name, c.parent_id AS cat_parent
                FROM {country}.service s
                JOIN {country}.category c ON c.id = s.category_id
                WHERE s.archive=false
                  AND s.canonical_key IS NOT NULL
                  AND s.name_embedding IS NOT NULL
                  AND c.archive=false
            """))).all()
            print(f"[{country}] services with full data: {len(rows)}")

        # Group: (category_name → ckey → list of services)
        by_cat: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        for r in rows:
            sid, sname, ck, emb, cat_id, cat_name, cat_parent = r
            clean_cat = _clean_category_name(cat_name)
            if _SKIP_CATEGORY_RE.search(clean_cat):
                continue
            ua_name = extract_uk_part_from_crm(sname or "")
            by_cat[clean_cat][ck].append({
                "id": sid,
                "name": ua_name,
                "raw_name": sname,
                "ckey": ck,
                "emb": parse_emb(emb),
            })
        print(f"[{country}] distinct categories (cleaned): {len(by_cat)}")

        # Build profiles per category з intra-cat clustering
        profiles_raw: list[dict] = []
        for cat_name, ck_to_svcs in sorted(by_cat.items()):
            ckeys = list(ck_to_svcs.keys())
            # Compute ckey centroid + popularity
            ck_data = []
            for ck in ckeys:
                svcs = ck_to_svcs[ck]
                centroid = np.mean([s["emb"] for s in svcs], axis=0)
                n = np.linalg.norm(centroid)
                if n > 0:
                    centroid /= n
                ck_data.append({
                    "ck": ck,
                    "centroid": centroid,
                    "svcs": svcs,
                    "count": len(svcs),
                })
            ck_data.sort(key=lambda x: -x["count"])
            embs = [d["centroid"] for d in ck_data]
            members_lists = _greedy_subcluster(
                [d["ck"] for d in ck_data], embs, args.split_threshold,
            )

            for cluster_idx, member_indices in enumerate(members_lists):
                cluster_cks = [ck_data[i]["ck"] for i in member_indices]
                cluster_svcs = [s for i in member_indices for s in ck_data[i]["svcs"]]
                if len(cluster_svcs) < args.min_cluster_svc:
                    continue
                profiles_raw.append({
                    "category": cat_name,
                    "cluster_idx": cluster_idx,
                    "ckeys": cluster_cks,
                    "svcs": cluster_svcs,
                })

        # Refine 2: gender override → reassign male services to "Чоловічі послуги"
        male_bucket_svcs: list = []
        male_bucket_cks: set[str] = set()
        for p in profiles_raw:
            keep_svcs = []
            for s in p["svcs"]:
                if _service_has_gender(s["name"], "male"):
                    male_bucket_svcs.append(s)
                    male_bucket_cks.add(s["ckey"])
                else:
                    keep_svcs.append(s)
            p["svcs"] = keep_svcs
            p["ckeys"] = sorted({s["ckey"] for s in keep_svcs})

        # Filter empty after gender extract
        profiles_raw = [p for p in profiles_raw if p["svcs"]]

        if male_bucket_svcs:
            profiles_raw.append({
                "category": "<gender bucket>",
                "cluster_idx": 0,
                "ckeys": sorted(male_bucket_cks),
                "svcs": male_bucket_svcs,
                "gender_bucket": "male",
            })

        # Naming: if single-cluster-per-category → category name; else derive from samples
        # consolidate profiles з ідентичним cluster name within country
        named: dict[str, dict] = {}
        for p in profiles_raw:
            if p.get("gender_bucket") == "male":
                name = "Чоловічі послуги"
            else:
                cat_clusters = [pp for pp in profiles_raw if pp["category"] == p["category"]
                                and not pp.get("gender_bucket")]
                if len(cat_clusters) == 1:
                    name = p["category"]
                else:
                    # find dominant ckey of cluster → use як name
                    top_ck = max(p["ckeys"], key=lambda c: sum(1 for s in p["svcs"] if s["ckey"]==c))
                    top_svc = next(s for s in p["svcs"] if s["ckey"] == top_ck)
                    name = _derive_profile_name(top_svc["name"], fallback=p["category"])
            if name in named:
                # merge into existing
                named[name]["svcs"].extend(p["svcs"])
                named[name]["ckeys"] = sorted(set(named[name]["ckeys"]) | set(p["ckeys"]))
            else:
                named[name] = {
                    "name": name,
                    "category": p["category"],
                    "svcs": list(p["svcs"]),
                    "ckeys": list(p["ckeys"]),
                    "gender_bucket": p.get("gender_bucket"),
                }

        # Build output
        out_profiles = []
        for name, p in sorted(named.items()):
            samples_by_ck: dict[str, str] = {}
            for s in p["svcs"]:
                samples_by_ck.setdefault(s["ckey"], s["raw_name"] or s["name"])
            descs = {ck: gen_key_description(sample) for ck, sample in samples_by_ck.items()}
            scope = "male" if p.get("gender_bucket") == "male" else profile_scope(name)
            out_profiles.append({
                "name": name,
                "country": country,
                "scope": scope,
                "category_origin": p["category"],
                "canonical_keys": p["ckeys"],
                "key_descriptions": descs,
                "sample_names": list(samples_by_ck.values())[:10],
                "service_count": len(p["svcs"]),
            })

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUT_DIR / f"{country}_profiles_v2.json"
        out.write_text(json.dumps(out_profiles, ensure_ascii=False, indent=2))

        print(f"\n=== Summary [{country}] ===")
        print(f"  Total proposed profiles: {len(out_profiles)}")
        sizes = sorted([len(p["canonical_keys"]) for p in out_profiles])
        print(f"  single-ckey: {sum(1 for s in sizes if s==1)}")
        print(f"  2-3 ckeys:   {sum(1 for s in sizes if 2<=s<=3)}")
        print(f"  4-10 ckeys:  {sum(1 for s in sizes if 4<=s<=10)}")
        print(f"  11-30 ckeys: {sum(1 for s in sizes if 11<=s<=30)}")
        print(f"  31+ ckeys:   {sum(1 for s in sizes if s>30)}")
        total_svc = sum(p["service_count"] for p in out_profiles)
        print(f"  Total services covered: {total_svc}")
        print(f"  Written: {out}")

        print(f"\n=== Top 20 profiles by service count ===")
        for p in sorted(out_profiles, key=lambda x: -x["service_count"])[:20]:
            sc = f"[{p['scope']}]" if p["scope"] else ""
            print(f"  {sc:9s} {p['name'][:45]:45s} {p['service_count']:4d} svc  {len(p['canonical_keys']):3d} ckeys")
    finally:
        await engine.dispose()


def _derive_profile_name(sample_ua: str, fallback: str) -> str:
    if not sample_ua:
        return fallback
    # Strip trailing tier + length + parens
    n = re.sub(r"\s*(?:МАЙСТЕР|ТОП|АРТ|БАРБЕР|МАСТЕР|JUNIOR|ДЖУНІОР)\s*$", "", sample_ua, flags=re.IGNORECASE)
    n = re.sub(r"\s*\d+\s*довжин\w*\s*$", "", n, flags=re.IGNORECASE)
    n = re.sub(r"\s*\(.*?\)\s*$", "", n)
    n = re.sub(r"\s{2,}", " ", n).strip()
    return n[:80] or fallback


if __name__ == "__main__":
    asyncio.run(amain())
