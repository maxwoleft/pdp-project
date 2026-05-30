"""Імпорт missing profiles з JSON. Skip якщо profile (country, name) вже існує."""
import argparse
import asyncio
import json
import uuid

from sqlalchemy import text

from app.infrastructure.db.session import build_engine, build_session_factory


async def amain() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--file", required=True)
    args = p.parse_args()

    with open(args.file) as f:
        rows = json.load(f)
    print(f"Loaded {len(rows)} profile bundles")

    engine = build_engine()
    factory = build_session_factory(engine)
    created = skipped = 0

    try:
        async with factory() as session:
            for row in rows:
                ex = await session.execute(text(
                    "SELECT id FROM public.service_profile WHERE country=:c AND name=:n"
                ), {"c": row["country"], "n": row["name"]})
                if ex.scalar():
                    skipped += 1
                    continue

                primary = row["canonical_key"]
                chk = await session.execute(text(
                    "SELECT id FROM public.service_profile WHERE country=:c AND canonical_key=:k"
                ), {"c": row["country"], "k": primary})
                if chk.scalar():
                    primary = f"import_{uuid.uuid4().hex[:12]}"

                new_pid = str(uuid.uuid4())
                await session.execute(text("""
                    INSERT INTO public.service_profile
                      (id, canonical_key, name, country, default_language, enabled,
                       salon_ids, cities, canonical_keys, key_descriptions, keywords_by_lang,
                       created_by, updated_by, current_version, created_at, updated_at)
                    VALUES
                      (:id, :ck, :name, :c, 'uk', true,
                       CAST(:s AS jsonb), CAST(:ct AS jsonb), CAST(:ks AS jsonb),
                       CAST(:kd AS jsonb), CAST(:kbl AS jsonb),
                       'import_v1', 'import_v1', 1, NOW(), NOW())
                """), {
                    "id": new_pid, "ck": primary, "name": row["name"], "c": row["country"],
                    "s": json.dumps(row.get("salon_ids") or []),
                    "ct": json.dumps(row.get("cities") or []),
                    "ks": json.dumps(row.get("canonical_keys") or []),
                    "kd": json.dumps(row.get("key_descriptions") or {}),
                    "kbl": json.dumps(row.get("keywords_by_lang") or {}),
                })
                # UK translation
                t = row.get("translation") or {}
                if t:
                    emb = t.get("embedding_str")
                    emb_cast = "CAST(:emb AS vector)" if emb else "NULL"
                    params = {
                        "pid": new_pid,
                        "sd": t.get("short_description") or "",
                        "ap": json.dumps(t.get("addresses_problems") or []),
                        "ta": json.dumps(t.get("target_audience") or []),
                        "bn": json.dumps(t.get("benefits") or []),
                        "kw": json.dumps(t.get("keywords") or []),
                        "sp": t.get("sales_pitch") or "",
                        "cs": json.dumps(t.get("cross_sell") or []),
                        "ps": json.dumps(t.get("procedure_steps") or []),
                        "ci": json.dumps(t.get("contraindications") or []),
                        "aa": t.get("aftercare_advice") or "",
                    }
                    if emb:
                        params["emb"] = emb
                    await session.execute(text(f"""
                        INSERT INTO public.service_profile_translation
                          (id, profile_id, language, short_description,
                           addresses_problems, target_audience, benefits, keywords,
                           sales_pitch, cross_sell, procedure_steps, contraindications,
                           aftercare_advice, embedding, created_at, updated_at)
                        VALUES
                          (gen_random_uuid(), :pid, 'uk', :sd,
                           CAST(:ap AS jsonb), CAST(:ta AS jsonb), CAST(:bn AS jsonb), CAST(:kw AS jsonb),
                           :sp, CAST(:cs AS jsonb), CAST(:ps AS jsonb), CAST(:ci AS jsonb),
                           :aa, {emb_cast}, NOW(), NOW())
                    """), params)
                created += 1
                print(f"  + {row['country']} {row['name']}")

            await session.commit()
            print(f"\nDONE: created={created} skipped(existing)={skipped}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
