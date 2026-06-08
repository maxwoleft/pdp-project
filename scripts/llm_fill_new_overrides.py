"""Phase 3: LLM auto-fill expert content для нових profiles що з'явилися після
template-fill (Phase 2). Cron-triggered після auto_link/link_by_category.

Тригер: profile.country in (ua,pl,gb) AND uk-translation відсутній АБО
translation.addresses_problems = [] (порожній — означає що template-fill не
покрив цей profile).

Виклик: gpt-5-mini з structured JSON output.

Запуск:
    python -m scripts.llm_fill_new_overrides --country ua --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import uuid

from openai import AsyncOpenAI
from sqlalchemy import text

from app.core.config import get_settings
from app.infrastructure.db.session import build_engine, build_session_factory

log = logging.getLogger("llm_overrides")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


SYSTEM_PROMPT = (
    "Ти експерт індустрії краси з 20+ роками досвіду у салонах + AI conversation designer. "
    "Завдання: для конкретного profile послуги салону згенерувати expert-контент який AI чатбот "
    "використовує щоб ПІДБИРАТИ цю послугу клієнтам у розмові.\n\n"
    "Вихід — STRICT JSON з полями:\n"
    "  addresses_problems: list[str]  — скарги/потреби клієнта на які підходить послуга (2-4 items)\n"
    "  target_audience: list[str]    — для кого підходить (2-4 items)\n"
    "  benefits: list[str]           — переваги/результат який клієнт отримає (2-4 items)\n"
    "  keywords: list[str]           — синоніми/розмовні форми запиту (4-8 items)\n"
    "  sales_pitch: str              — коротка фраза-презентація (1 речення)\n"
    "  cross_sell: list[str]         — що ще порадити разом (2-4 items)\n\n"
    "Мова: українська. Конкретно, без води. Жодних emoji."
)


async def generate_content(
    client: AsyncOpenAI, model: str, profile_name: str, sample_services: list[str]
) -> dict | None:
    user_prompt = (
        f"Profile: '{profile_name}'\n"
        f"Приклади послуг у цьому profile:\n"
        + "\n".join(f"- {s}" for s in sample_services[:5])
        + "\n\nЗгенеруй JSON expert-контент."
    )
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            max_completion_tokens=800,
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw)
        # Validate keys
        required = ["addresses_problems", "target_audience", "benefits",
                    "keywords", "sales_pitch", "cross_sell"]
        for k in required:
            if k not in data:
                log.warning("Missing key '%s' in LLM output for '%s'", k, profile_name)
                return None
        return data
    except Exception as exc:
        log.warning("LLM call failed for '%s': %s", profile_name, exc)
        return None


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", required=True, choices=["ua", "pl", "gb"])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--model", default="gpt-5-mini")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--language", default="uk")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    settings = get_settings()
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            # Find profiles without expert content (translation missing OR addresses_problems empty)
            r = await session.execute(text(f"""
                SELECT p.id, p.name,
                  COALESCE(
                    (SELECT array_agg(s.name) FROM (
                       SELECT name FROM {args.country}.service
                       WHERE archive=false AND profile_id = p.id::text
                       ORDER BY name LIMIT 5
                     ) s),
                    ARRAY[]::text[]
                  ) AS samples
                FROM public.service_profile p
                LEFT JOIN public.service_profile_translation t
                  ON t.profile_id = p.id AND t.language = :lang
                WHERE p.country = :c
                  AND (t.id IS NULL
                       OR COALESCE(t.addresses_problems, '[]'::json)::text = '[]')
                ORDER BY p.name
            """), {"c": args.country, "lang": args.language})
            rows = r.all()
            log.info("Profiles needing LLM fill: %d", len(rows))

            if args.limit:
                rows = rows[: args.limit]

            if not args.apply:
                for pid, pname, samples in rows[:10]:
                    log.info("  %s | %d samples", pname, len(samples or []))
                log.info("DRY RUN")
                return

            sem = asyncio.Semaphore(args.concurrency)
            done = [0]

            async def process_one(pid: str, pname: str, samples: list[str]) -> None:
                async with sem:
                    if not samples:
                        log.info("  %s: no services, skipping", pname)
                        return
                    content = await generate_content(client, args.model, pname, list(samples))
                    if not content:
                        return
                    # UPSERT translation
                    existing = (await session.execute(text(
                        "SELECT id FROM public.service_profile_translation "
                        "WHERE profile_id=:pid AND language=:lang"
                    ), {"pid": pid, "lang": args.language})).first()
                    payload = {
                        "pid": pid, "lang": args.language,
                        "sd": content.get("sales_pitch") or pname,
                        "ap": json.dumps(content["addresses_problems"], ensure_ascii=False),
                        "ta": json.dumps(content["target_audience"], ensure_ascii=False),
                        "b": json.dumps(content["benefits"], ensure_ascii=False),
                        "kw": json.dumps(content["keywords"], ensure_ascii=False),
                        "sp": content.get("sales_pitch") or None,
                        "cs": json.dumps(content["cross_sell"], ensure_ascii=False),
                    }
                    if existing:
                        await session.execute(text("""
                            UPDATE public.service_profile_translation SET
                              short_description = :sd,
                              addresses_problems = CAST(:ap AS json),
                              target_audience = CAST(:ta AS json),
                              benefits = CAST(:b AS json),
                              keywords = CAST(:kw AS json),
                              sales_pitch = :sp,
                              cross_sell = CAST(:cs AS json),
                              updated_at = NOW()
                            WHERE profile_id = :pid AND language = :lang
                        """), payload)
                    else:
                        payload["id"] = str(uuid.uuid4())
                        await session.execute(text("""
                            INSERT INTO public.service_profile_translation
                            (id, profile_id, language, short_description, detailed_description,
                             addresses_problems, target_audience, benefits, keywords,
                             procedure_steps, contraindications, cross_sell,
                             sales_pitch, ckey_overrides,
                             created_at, updated_at)
                            VALUES (:id, :pid, :lang, :sd, NULL,
                                    CAST(:ap AS json), CAST(:ta AS json),
                                    CAST(:b AS json), CAST(:kw AS json),
                                    '[]'::json, '[]'::json, CAST(:cs AS json),
                                    :sp, '{}'::jsonb,
                                    NOW(), NOW())
                        """), payload)
                    done[0] += 1
                    if done[0] % 5 == 0:
                        log.info("  progress: %d / %d", done[0], len(rows))

            await asyncio.gather(*[process_one(*item) for item in rows])
            await session.commit()
            log.info("DONE: %d profiles filled via LLM", done[0])
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
