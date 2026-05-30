"""Імпорт translations з JSON у БД. Match by (country, name) → profile_id.
UPSERT — overwrite existing translations.

Запуск:
    python -m scripts.import_translations_from_json --file .logs/translations/local_translations.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging

from sqlalchemy import text

from app.infrastructure.db.session import build_engine, build_session_factory

log = logging.getLogger("import")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    args = parser.parse_args()

    with open(args.file) as f:
        rows = json.load(f)
    log.info("Loaded %d rows", len(rows))

    engine = build_engine()
    factory = build_session_factory(engine)
    matched = 0
    skipped = 0
    inserted = 0
    updated = 0

    try:
        async with factory() as session:
            # Build (country, name) → profile_id map
            pid_rows = await session.execute(text(
                "SELECT id, country, name FROM public.service_profile"
            ))
            pid_map: dict[tuple[str, str], str] = {
                (r[1], r[2]): r[0] for r in pid_rows.all()
            }
            log.info("Profile map: %d entries", len(pid_map))

            for row in rows:
                key = (row["country"], row["name"])
                pid = pid_map.get(key)
                if not pid:
                    skipped += 1
                    continue
                matched += 1

                emb_str = row.get("embedding_str")
                emb_cast = "CAST(:emb AS vector)" if emb_str else "NULL"

                # Try update first
                upd_sql = text(f"""
                    UPDATE public.service_profile_translation
                    SET short_description=:sd, addresses_problems=CAST(:ap AS jsonb),
                        target_audience=CAST(:ta AS jsonb), benefits=CAST(:bn AS jsonb),
                        keywords=CAST(:kw AS jsonb), sales_pitch=:sp,
                        cross_sell=CAST(:cs AS jsonb), procedure_steps=CAST(:ps AS jsonb),
                        contraindications=CAST(:ci AS jsonb), aftercare_advice=:aa,
                        embedding={emb_cast},
                        updated_at=NOW()
                    WHERE profile_id=:pid AND language=:lang
                """)
                params = {
                    "pid": pid, "lang": row["language"],
                    "sd": row["short_description"] or "",
                    "ap": json.dumps(row["addresses_problems"] or []),
                    "ta": json.dumps(row["target_audience"] or []),
                    "bn": json.dumps(row["benefits"] or []),
                    "kw": json.dumps(row["keywords"] or []),
                    "sp": row["sales_pitch"] or "",
                    "cs": json.dumps(row["cross_sell"] or []),
                    "ps": json.dumps(row["procedure_steps"] or []),
                    "ci": json.dumps(row["contraindications"] or []),
                    "aa": row["aftercare_advice"] or "",
                }
                if emb_str:
                    params["emb"] = emb_str
                r = await session.execute(upd_sql, params)
                if r.rowcount and r.rowcount > 0:
                    updated += 1
                else:
                    # Insert
                    ins_sql = text(f"""
                        INSERT INTO public.service_profile_translation
                            (id, profile_id, language, short_description,
                             addresses_problems, target_audience, benefits, keywords,
                             sales_pitch, cross_sell, procedure_steps, contraindications,
                             aftercare_advice, embedding, created_at, updated_at)
                        VALUES
                            (gen_random_uuid(), :pid, :lang, :sd,
                             CAST(:ap AS jsonb), CAST(:ta AS jsonb), CAST(:bn AS jsonb), CAST(:kw AS jsonb),
                             :sp, CAST(:cs AS jsonb), CAST(:ps AS jsonb), CAST(:ci AS jsonb),
                             :aa, {emb_cast}, NOW(), NOW())
                    """)
                    await session.execute(ins_sql, params)
                    inserted += 1

            await session.commit()
            log.info("DONE: matched=%d, updated=%d, inserted=%d, skipped(no profile match)=%d",
                     matched, updated, inserted, skipped)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
