"""Step 1 (rewrite plan): знайти clusters canonical_keys у країні, які semantically
ймовірно одна послуга з різними spelling/synonym.

Метод:
  1. Для кожного distinct canonical_key — взяти усі services з name_embedding,
     обчислити mean-embedding.
  2. Cosine similarity матриця між ckeys.
  3. Union-find з threshold (default 0.92).
  4. Output: clusters з ≥2 ckey з sample names + service counts.

Запуск:
    python -m scripts.find_ckey_clusters --country ua --threshold 0.92
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import numpy as np
from sqlalchemy import text

from app.infrastructure.db.session import build_engine, build_session_factory

OUT_DIR = Path(".logs/ckey_clusters")

# Discriminator tokens — якщо present у одному ckey і відсутній у іншому,
# merge заборонено. Це усуває false-positive embedding clusters між
# manikyur↔pedykyur, gender, partial vs full, тощо.
DISCRIMINATORS: list[set[str]] = [
    {"manikyur", "manicure"},                   # манікюр vs все інше
    {"pedykyur", "pedicure", "pedykir"},        # педикюр vs все інше
    {"stryzhka", "haircut"},                    # стрижка
    {"farbuvannya", "tonuvannya", "dyeing"},    # фарбування vs не
    {"chol", "men", "muzh", "cholovich", "cholovichyi"},  # чоловіче
    {"zhin", "zhinochyi", "wom", "women"},      # жіноче
    {"dyt", "dit", "child", "kid", "dziec"},    # дитяче
    {"chastkovyi", "partial"},                  # частковий vs повний
    {"povnyi", "povna", "full"},                # повний/full
    {"ekspres", "express"},                     # експрес vs повний
    {"naroshchuvannya", "extension"},           # нарощування
    {"znyattya", "removal"},                    # зняття
    {"korektsiya", "correction"},               # корекція
    {"masazh", "massage"},                      # масаж
    {"depilyatsiya", "shugaring", "vosk", "wax"},  # депіляція методи
    {"laminuvannya", "lamination"},             # ламінування
    {"piling", "peeling"},                      # пілінг
    {"likuvannya", "treatment"},                # лікування
    {"chystka", "cleansing"},                   # чистка
    {"makiyazh", "makeup"},                     # макіяж
    {"briv", "brow", "eyebrow"},                # брови
    {"vii", "lash", "eyelash"},                 # вії
]


def _ckey_tokens(ckey: str) -> set[str]:
    return {t for t in ckey.split("_") if t and len(t) >= 3}


def _violates_discriminators(a: str, b: str) -> bool:
    """True якщо ckeys мають discriminator token у одному але не іншому."""
    ta = _ckey_tokens(a)
    tb = _ckey_tokens(b)
    for group in DISCRIMINATORS:
        in_a = bool(ta & group)
        in_b = bool(tb & group)
        if in_a != in_b:
            return True
    return False


async def amain() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--country", required=True, choices=["ua", "pl", "gb"])
    p.add_argument("--threshold", type=float, default=0.92)
    p.add_argument("--min-svc", type=int, default=2,
                   help="Skip ckeys з менше ніж N services (зменшує шум)")
    args = p.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            rows = (await session.execute(text(f"""
                SELECT canonical_key,
                       COUNT(*) AS cnt,
                       MIN(name) AS sample,
                       AVG(name_embedding) AS mean_emb
                FROM {args.country}.service
                WHERE archive=false
                  AND canonical_key IS NOT NULL
                  AND name_embedding IS NOT NULL
                GROUP BY canonical_key
                HAVING COUNT(*) >= :min_svc
                ORDER BY COUNT(*) DESC
            """), {"min_svc": args.min_svc})).all()

        print(f"[{args.country}] ckeys ≥{args.min_svc} svc with embeddings: {len(rows)}")
        if not rows:
            return

        ckeys = [r[0] for r in rows]
        counts = [r[1] for r in rows]
        samples = [r[2] for r in rows]
        # mean_emb returns string-form vector "[0.1, 0.2, ...]"; parse to numpy
        embs = []
        for r in rows:
            raw = r[3]
            if isinstance(raw, str):
                vec = json.loads(raw)
            else:
                vec = list(raw)
            embs.append(np.array(vec, dtype=np.float32))
        M = np.vstack(embs)
        # Normalize for cosine
        norms = np.linalg.norm(M, axis=1, keepdims=True)
        norms[norms == 0] = 1
        M = M / norms

        sim = M @ M.T
        n = len(ckeys)

        # Union-find
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        pairs = 0
        vetoed = 0
        for i in range(n):
            for j in range(i + 1, n):
                if sim[i, j] >= args.threshold:
                    if _violates_discriminators(ckeys[i], ckeys[j]):
                        vetoed += 1
                        continue
                    union(i, j)
                    pairs += 1
        print(f"[{args.country}] pairs merged: {pairs}, vetoed: {vetoed}")

        # Build clusters
        clusters: dict[int, list[int]] = {}
        for i in range(n):
            clusters.setdefault(find(i), []).append(i)

        multi = {k: v for k, v in clusters.items() if len(v) >= 2}
        print(f"[{args.country}] clusters з ≥2 ckey: {len(multi)}")

        # Output
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_file = OUT_DIR / f"{args.country}_clusters.json"
        result = []
        for cid, members in sorted(multi.items(), key=lambda kv: -sum(counts[i] for i in kv[1])):
            # propose canonical form: ckey з найбільшою кількістю services
            members_sorted = sorted(members, key=lambda i: -counts[i])
            proposed = ckeys[members_sorted[0]]
            entries = []
            for i in members_sorted:
                # avg sim to proposed (members_sorted[0])
                avg_sim = float(sim[members_sorted[0], i])
                entries.append({
                    "ckey": ckeys[i],
                    "sample": samples[i],
                    "count": counts[i],
                    "sim_to_proposed": round(avg_sim, 3),
                })
            result.append({
                "proposed_canonical": proposed,
                "total_services": sum(counts[i] for i in members),
                "members": entries,
            })
        out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"[{args.country}] written: {out_file}")

        # Console preview top 20 by impact
        print(f"\n=== TOP 20 clusters by total services ({args.country}) ===")
        for c in result[:20]:
            print(f"\n→ proposed: {c['proposed_canonical']}  (total {c['total_services']} svc)")
            for m in c['members']:
                marker = "★" if m['ckey'] == c['proposed_canonical'] else " "
                print(f"  {marker} [{m['sim_to_proposed']}] {m['ckey'][:50]:50s} | {m['count']:3d} | {(m['sample'] or '')[:70]}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
