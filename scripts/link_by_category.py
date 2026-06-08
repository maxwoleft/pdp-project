"""Fallback linking: для services з profile_id=NULL знайти profile за
CRM-категорією. Призначає profile_id. Якщо service.canonical_key не у
profile.canonical_keys[] — додає туди + gen description.

Запуск:
    python -m scripts.link_by_category --country ua --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re

from sqlalchemy import text

from app.domain.services.canonical_key import extract_uk_part_from_crm
from app.infrastructure.db.session import build_engine, build_session_factory
from scripts.apply_profiles_v3 import _normalize_name
from scripts.auto_link_missing_keys import gen_key_description


async def amain() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--country", required=True, choices=["ua", "pl", "gb"])
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)
    country = args.country
    import json as _json
    import numpy as _np

    def _parse_vec(raw):
        if raw is None:
            return None
        v = _json.loads(raw) if isinstance(raw, str) else list(raw)
        arr = _np.array(v, dtype=_np.float32)
        n = _np.linalg.norm(arr)
        return arr / n if n > 0 else arr

    try:
        async with factory() as session:
            # Build category-name → profile_id + profile centroid (mean embedding of linked services).
            profiles = (await session.execute(text(f"""
                SELECT sp.id, sp.name, AVG(s.name_embedding) AS centroid
                FROM public.service_profile sp
                LEFT JOIN {country}.service s ON s.profile_id = sp.id::text
                       AND s.archive=false AND s.name_embedding IS NOT NULL
                WHERE sp.country=:c
                GROUP BY sp.id, sp.name
            """), {"c": country})).all()
            profile_by_name: dict[str, str] = {}
            profile_centroids: list[tuple[str, str, _np.ndarray | None]] = []
            for pid, pname, cent in profiles:
                profile_by_name[(pname or "").lower().strip()] = str(pid)
                profile_centroids.append((str(pid), pname or "", _parse_vec(cent)))

            # Unlinked services with their category + embedding.
            rows = (await session.execute(text(f"""
                SELECT s.id, s.canonical_key, s.name, c.name AS cat_name,
                       s.name_embedding
                FROM {country}.service s
                JOIN {country}.category c ON c.id = s.category_id
                WHERE s.archive=false AND s.profile_id IS NULL
                  AND c.archive=false
            """))).all()
            print(f"[{country}] unlinked services: {len(rows)}")

            linked_cnt = 0
            no_match_cnt = 0
            added_ckeys: dict[str, set[str]] = {}  # profile_id → ckeys to add
            updates: list[tuple[str, str]] = []  # (service_id, profile_id)

            for sid, ck, sname, cat_raw, emb in rows:
                cat_clean = _normalize_name(extract_uk_part_from_crm(cat_raw or ""))
                target_pid = profile_by_name.get(cat_clean.lower())
                if not target_pid:
                    # Fallback 1: split-by-"/" → перша частина (для GB-style
                    # склеєних categori "Жіночі укладки/зачіски" → "Жіночі укладки").
                    first_part = cat_clean.split("/")[0].strip()
                    if first_part:
                        target_pid = profile_by_name.get(first_part.lower())
                if not target_pid:
                    # Fallback 2: token-overlap match. Знаходимо profile з
                    # найбільшою кількістю спільних значущих токенів з category.
                    import re as _re
                    cat_tokens = {t for t in _re.findall(r"\w+", cat_clean.lower()) if len(t) >= 3}
                    best_overlap = 0
                    best_id = None
                    for p_low, p_id in profile_by_name.items():
                        if not p_low:
                            continue
                        p_tokens = {t for t in _re.findall(r"\w+", p_low) if len(t) >= 3}
                        overlap = len(cat_tokens & p_tokens)
                        if overlap > best_overlap:
                            best_overlap = overlap
                            best_id = p_id
                    if best_overlap >= 2:
                        target_pid = best_id
                if not target_pid and emb is not None:
                    # Fallback 3: embedding similarity до profile centroid.
                    svc_vec = _parse_vec(emb)
                    if svc_vec is not None:
                        best_sim = 0.0
                        best_id = None
                        for c_pid, c_pname, c_cent in profile_centroids:
                            if c_cent is None:
                                continue
                            sim = float(svc_vec @ c_cent)
                            if sim > best_sim:
                                best_sim = sim
                                best_id = c_pid
                        if best_id and best_sim >= 0.40:
                            target_pid = best_id
                if not target_pid:
                    no_match_cnt += 1
                    continue
                updates.append((sid, target_pid))
                if ck:
                    added_ckeys.setdefault(target_pid, set()).add(ck)
                linked_cnt += 1

            print(f"[{country}] will link: {linked_cnt}, no category-profile match: {no_match_cnt}")
            print(f"[{country}] profiles to receive new ckeys: {len(added_ckeys)}")

            if not args.apply:
                print("\nDRY RUN. Use --apply.")
                return

            # Add missing ckeys to profile.canonical_keys[] + descriptions
            for pid, ckeys in added_ckeys.items():
                # Get current profile state
                p = (await session.execute(text(
                    "SELECT canonical_keys, key_descriptions FROM public.service_profile WHERE id=:id"
                ), {"id": pid})).first()
                ck_list = set(p[0] or [])
                kd = dict(p[1] or {})
                new_cks = ckeys - ck_list
                if not new_cks:
                    continue
                # Sample name per new ckey
                for new_ck in new_cks:
                    sample = (await session.execute(text(f"""
                        SELECT name FROM {country}.service
                        WHERE canonical_key = :ck AND archive=false LIMIT 1
                    """), {"ck": new_ck})).scalar()
                    kd.setdefault(new_ck, gen_key_description(sample or new_ck))
                ck_list.update(new_cks)
                await session.execute(text("""
                    UPDATE public.service_profile
                    SET canonical_keys = CAST(:ck AS jsonb),
                        key_descriptions = CAST(:kd AS jsonb)
                    WHERE id = :id
                """), {
                    "ck": json.dumps(sorted(ck_list)),
                    "kd": json.dumps(kd),
                    "id": pid,
                })

            # Link services
            for sid, pid in updates:
                await session.execute(text(f"""
                    UPDATE {country}.service SET profile_id = :pid WHERE id = :sid
                """), {"pid": pid, "sid": sid})
            await session.commit()
            print(f"\n[{country}] APPLIED: {linked_cnt} services linked, "
                  f"{sum(len(v) for v in added_ckeys.values())} new ckey-profile relations added.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
