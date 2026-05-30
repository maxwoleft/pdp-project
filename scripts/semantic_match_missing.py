"""Semantic matching: для unmatched описів з descServ.json знаходить
найближчий canonical_key в БД через embedding cosine similarity.

Алгоритм:
1. Embed кожну unmatched desc назву
2. Embed кожну sample name з DB canonical keys
3. Cosine similarity → top-1 match з threshold ≥ 0.7
4. Виводить matched пари для review або --apply записує в БД

Запуск:
    python -m scripts.semantic_match_missing              # показати пари
    python -m scripts.semantic_match_missing --apply      # створити профілі
"""
from __future__ import annotations

import argparse
import asyncio
import json

import numpy as np
from sqlalchemy import text as sql_text

from app.adapters.embeddings.openai_embedder import OpenAIEmbedder
from app.domain.services.canonical_key import normalize_to_canonical_key
from app.infrastructure.db.repositories.profile_repo import ServiceProfileRepository
from app.infrastructure.db.session import build_engine, build_session_factory
from scripts.extracted_profiles import EXTRACTED_PROFILES
from scripts.seed_profiles_from_desc import fuzzy_match_db_key, clean_service_name


SIMILARITY_THRESHOLD = 0.65  # мінімальна cosine similarity для match


def cosine_sim(a: list[float], b: list[float]) -> float:
    a_np = np.array(a)
    b_np = np.array(b)
    return float(np.dot(a_np, b_np) / (np.linalg.norm(a_np) * np.linalg.norm(b_np) + 1e-9))


async def amain():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--threshold", type=float, default=SIMILARITY_THRESHOLD)
    args = parser.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)
    embedder = OpenAIEmbedder()

    try:
        async with factory() as session:
            # 1. Зібрати всі DB canonical_keys з sample names
            db_keys_info: dict[str, str] = {}  # key → sample_name
            for c in ["ua", "pl", "gb"]:
                sql = sql_text(
                    f"""
                    SELECT canonical_key,
                           (array_agg(COALESCE(name_uk, name) ORDER BY name))[1]
                    FROM {c}.service
                    WHERE canonical_key IS NOT NULL AND archive = false
                    GROUP BY canonical_key
                    """
                )
                for row in (await session.execute(sql)).fetchall():
                    if row[0] not in db_keys_info:
                        db_keys_info[row[0]] = row[1]

            # 2. Знайти unmatched desc entries
            all_db_keys = set(db_keys_info.keys())

            # Зібрати вже існуючі profile keys
            repo = ServiceProfileRepository(session, embedder=embedder)
            existing_profiles = await repo.list_all()
            existing_keys = {p.canonical_key for p in existing_profiles}

            with open("descServ.json", "r") as f:
                lines = f.readlines()

            unmatched = []
            for line in lines:
                obj = json.loads(line.strip())
                name = obj.get("service", "")
                if not name or name == "Послуга":
                    continue
                variants = [v.strip() for v in name.split("\n") if v.strip()]
                all_keys = set()
                for v in variants:
                    k = normalize_to_canonical_key(v)
                    if k:
                        all_keys.add(k)
                matched = fuzzy_match_db_key(all_keys, all_db_keys)
                if not matched:
                    unmatched.append({
                        "name": variants[0],
                        "all_names": variants,
                        "keys": all_keys,
                        "desc": obj.get("description", ""),
                        "desc2": obj.get("description2", ""),
                    })

            print(f"Unmatched desc entries: {len(unmatched)}")
            print(f"DB canonical_keys: {len(db_keys_info)}")
            print(f"Already have profiles: {len(existing_keys)}")

            # 3. Embed unmatched names
            print("\nEmbedding unmatched names...")
            unmatched_texts = [u["name"] for u in unmatched]
            unmatched_embs = await embedder.embed_batch(unmatched_texts)

            # 4. Embed DB sample names (only those without profiles yet)
            db_without_profile = {
                k: v for k, v in db_keys_info.items() if k not in existing_keys
            }
            print(f"DB keys without profiles to match against: {len(db_without_profile)}")

            db_names_list = list(db_without_profile.items())  # [(key, sample_name), ...]
            db_texts = [name for _, name in db_names_list]

            # Batch embed DB names
            print("Embedding DB sample names...")
            BATCH = 100
            db_embs = []
            for i in range(0, len(db_texts), BATCH):
                chunk = db_texts[i:i + BATCH]
                chunk_embs = await embedder.embed_batch(chunk)
                db_embs.extend(chunk_embs)

            # 5. Cosine similarity matrix
            print("Computing similarities...")
            matched_pairs = []
            for i, (u_entry, u_emb) in enumerate(zip(unmatched, unmatched_embs)):
                best_sim = 0.0
                best_db_key = None
                best_db_name = None
                for j, ((db_key, db_name), db_emb) in enumerate(zip(db_names_list, db_embs)):
                    sim = cosine_sim(u_emb, db_emb)
                    if sim > best_sim:
                        best_sim = sim
                        best_db_key = db_key
                        best_db_name = db_name

                if best_sim >= args.threshold and best_db_key:
                    matched_pairs.append({
                        "desc_name": u_entry["name"],
                        "db_key": best_db_key,
                        "db_name": best_db_name,
                        "similarity": round(best_sim, 3),
                        "desc": u_entry["desc"],
                        "desc2": u_entry["desc2"],
                    })
                    print(f"  ✓ {best_sim:.3f}  '{u_entry['name'][:50]}' → {best_db_key} ('{best_db_name[:50]}')")
                else:
                    print(f"  ✗ {best_sim:.3f}  '{u_entry['name'][:50]}' → no match (best: '{best_db_name[:40] if best_db_name else '?'}')")

            print(f"\nSemantic matches (≥{args.threshold}): {len(matched_pairs)}/{len(unmatched)}")

            if not args.apply:
                print("(DRY RUN — use --apply to create profiles)")
                await engine.dispose()
                return

            # 6. Створюємо профілі для matched
            created = 0
            for pair in matched_pairs:
                existing = await repo.get_by_canonical_key(pair["db_key"])
                if existing:
                    continue

                clean_name = clean_service_name(pair["desc_name"])
                # Шукаємо extracted fields
                ext_fields = EXTRACTED_PROFILES.get(pair["desc_name"], {})

                short_desc = ext_fields.get("short_description", "")[:500]
                if not short_desc:
                    short_desc = pair["desc"][:300] if pair["desc"] else clean_name

                aftercare = ext_fields.get("aftercare_advice")
                if isinstance(aftercare, list):
                    aftercare = "; ".join(str(x) for x in aftercare) if aftercare else None

                profile = await repo.create(
                    canonical_key=pair["db_key"],
                    name=clean_name,
                    country=None,
                    default_language="uk",
                    enabled=True,
                    created_by="semantic_match",
                    updated_by="semantic_match",
                )
                await repo.upsert_translation(
                    profile.id,
                    "uk",
                    short_description=short_desc,
                    detailed_description=str(pair["desc"] or short_desc),
                    addresses_problems=ext_fields.get("addresses_problems", []),
                    target_audience=ext_fields.get("target_audience", []),
                    benefits=ext_fields.get("benefits", []),
                    keywords=ext_fields.get("keywords", []),
                    sales_pitch=ext_fields.get("sales_pitch"),
                    cross_sell=ext_fields.get("cross_sell", []),
                    procedure_steps=ext_fields.get("procedure_steps", []),
                    contraindications=ext_fields.get("contraindications", []),
                    aftercare_advice=aftercare,
                )
                await repo.save_version(
                    profile.id,
                    change_summary=f"Semantic match ({pair['similarity']:.3f}) from descServ.json",
                    created_by="semantic_match",
                )
                created += 1

            await session.commit()
            print(f"\nCreated {created} profiles via semantic matching")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
