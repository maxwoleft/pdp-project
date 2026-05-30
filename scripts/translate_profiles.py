"""Професійний переклад UA profile content → RU / EN / PL.

Логіка:
- Беремо ServiceProfileTranslation language='uk'
- Для кожної target_lang ∈ ('ru', 'en', 'pl'):
  - skip if вже існує
  - Будуємо structured prompt з beauty-industry tone guidance
  - LLM call (gpt-5-mini) з JSON output schema
  - Зберігаємо результат як ServiceProfileTranslation з embedding

Tone:
- RU: формальное «вы», профессиональный салонный тон, без жаргонизмов
- EN: warm professional beauty industry English (use "you")
- PL: formalne «Pani/Pan», naturalna polszczyzna salonu beauty

Запуск:
    python -m scripts.translate_profiles --apply               # всі profile
    python -m scripts.translate_profiles --apply --lang en     # тільки EN
    python -m scripts.translate_profiles --apply --limit 10    # для тесту
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from typing import Any

from openai import AsyncOpenAI
from sqlalchemy import select, text

from app.core.config import get_settings
from app.infrastructure.db.models.profile import (
    ServiceProfile, ServiceProfileTranslation,
)
from app.infrastructure.db.session import build_engine, build_session_factory

log = logging.getLogger("translate")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


LANGUAGE_GUIDANCE: dict[str, str] = {
    "ru": (
        "Translate to Russian. Use professional beauty-salon tone with formal «вы». "
        "Keep Master-Sensei voice — calm expert advisor, not a robot. "
        "NO emoji, NO exclamation marks. Use natural Russian phrasing — "
        "don't word-translate Ukrainian, adapt to native Russian beauty-industry style. "
        "Keep all brand names (Lebel, Brae, Casmara, DMK etc.) in original form."
    ),
    "en": (
        "Translate to English. Use warm professional beauty-industry tone, address client with 'you'. "
        "Keep Master-Sensei voice — calm expert advisor, not a robot. "
        "NO emoji, NO exclamation marks. Use natural beauty-salon English — "
        "don't word-translate from Ukrainian, adapt to UK/US beauty-industry standard phrasing. "
        "Keep all brand names (Lebel, Brae, Casmara, DMK etc.) in original form."
    ),
    "pl": (
        "Tłumacz na język polski. Użyj profesjonalnego tonu salonu beauty, formalna forma «Pani/Pan». "
        "Zachowaj głos Mistrza-Sensei — spokojny ekspert-doradca, nie robot. "
        "BEZ emoji, BEZ wykrzykników. Naturalna polszczyzna branży beauty — "
        "nie tłumacz dosłownie z ukraińskiego, dostosuj do polskich standardów językowych branży. "
        "Zachowaj nazwy marek (Lebel, Brae, Casmara, DMK itd.) w oryginalnej formie."
    ),
}


# JSON schema для structured output — fields збігаються з ServiceProfileTranslation
TRANSLATION_SCHEMA = {
    "type": "object",
    "properties": {
        "short_description": {"type": "string"},
        "addresses_problems": {"type": "array", "items": {"type": "string"}},
        "target_audience": {"type": "array", "items": {"type": "string"}},
        "benefits": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "sales_pitch": {"type": "string"},
        "cross_sell": {"type": "array", "items": {"type": "string"}},
        "procedure_steps": {"type": "array", "items": {"type": "string"}},
        "contraindications": {"type": "array", "items": {"type": "string"}},
        "aftercare_advice": {"type": "string"},
    },
    "required": [
        "short_description", "addresses_problems", "target_audience",
        "benefits", "keywords", "sales_pitch", "cross_sell",
        "procedure_steps", "contraindications", "aftercare_advice",
    ],
    "additionalProperties": False,
}


def _to_dict(t: ServiceProfileTranslation) -> dict[str, Any]:
    return {
        "short_description": t.short_description or "",
        "addresses_problems": list(t.addresses_problems or []),
        "target_audience": list(t.target_audience or []),
        "benefits": list(t.benefits or []),
        "keywords": list(t.keywords or []),
        "sales_pitch": t.sales_pitch or "",
        "cross_sell": list(t.cross_sell or []),
        "procedure_steps": list(t.procedure_steps or []),
        "contraindications": list(t.contraindications or []),
        "aftercare_advice": t.aftercare_advice or "",
    }


async def translate_one(
    client: AsyncOpenAI,
    model: str,
    profile_name: str,
    src_payload: dict[str, Any],
    target_lang: str,
) -> dict[str, Any] | None:
    guidance = LANGUAGE_GUIDANCE[target_lang]
    system_prompt = (
        f"You are a professional beauty-industry translator. "
        f"You translate service profiles for a high-end beauty salon network "
        f"(Ukraine, Poland, UK). Translation must read as if written by a "
        f"native expert copywriter — never as machine output.\n\n"
        f"{guidance}\n\n"
        f"Preserve technical accuracy of procedures and contraindications. "
        f"For keywords[] — use the words clients would actually type when searching, "
        f"in the target language. "
        f"Output strict JSON matching the provided schema. "
        f"Do NOT add commentary. Do NOT keep Ukrainian text."
    )
    user_prompt = (
        f"Profile name: {profile_name}\n"
        f"Source language: Ukrainian\n"
        f"Target language: {target_lang.upper()}\n\n"
        f"SOURCE (JSON):\n{json.dumps(src_payload, ensure_ascii=False, indent=2)}\n\n"
        f"Return ONLY the translated JSON (same schema)."
    )

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "translation",
                    "schema": TRANSLATION_SCHEMA,
                    "strict": True,
                },
            },
        )
        content = resp.choices[0].message.content
        if not content:
            return None
        return json.loads(content)
    except Exception as exc:
        log.warning("Translation failed for %s → %s: %s", profile_name, target_lang, exc)
        return None


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--lang", choices=["ru", "en", "pl"], default=None,
                        help="Translate only this language (default: all 3)")
    parser.add_argument("--country", choices=["ua", "pl", "gb"], default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--model", default="gpt-5-mini")
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()

    settings = get_settings()
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    target_langs = [args.lang] if args.lang else ["ru", "en", "pl"]

    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            stmt = select(ServiceProfile).order_by(ServiceProfile.country, ServiceProfile.name)
            if args.country:
                stmt = stmt.where(ServiceProfile.country == args.country)
            if args.limit:
                stmt = stmt.limit(args.limit)
            profiles = list((await session.execute(stmt)).scalars().unique().all())
            log.info("Profiles: %d, target langs: %s, model: %s", len(profiles), target_langs, args.model)

            # Витягуємо UK translation для кожного
            tasks_q: asyncio.Queue = asyncio.Queue()
            for p in profiles:
                uk_t = next((t for t in p.translations if t.language == "uk"), None)
                if not uk_t:
                    log.warning("Profile %s has no UK translation — skip", p.name)
                    continue
                existing_langs = {t.language for t in p.translations}
                for lang in target_langs:
                    if lang in existing_langs:
                        continue
                    tasks_q.put_nowait((p, uk_t, lang))

            total = tasks_q.qsize()
            log.info("Translation tasks: %d", total)
            if not args.apply:
                log.info("DRY RUN. Pass --apply to execute.")
                return

            await session.close()  # release session — we'll open per-save

            done_count = [0]
            saved_count = [0]
            sem = asyncio.Semaphore(args.concurrency)
            save_lock = asyncio.Lock()

            async def save_translation(pid: str, lang: str, payload: dict[str, Any]) -> None:
                async with save_lock, factory() as save_sess:
                    save_sess.add(ServiceProfileTranslation(
                        profile_id=pid,
                        language=lang,
                        short_description=payload["short_description"],
                        addresses_problems=payload["addresses_problems"],
                        target_audience=payload["target_audience"],
                        benefits=payload["benefits"],
                        keywords=payload["keywords"],
                        sales_pitch=payload["sales_pitch"],
                        cross_sell=payload["cross_sell"],
                        procedure_steps=payload["procedure_steps"],
                        contraindications=payload["contraindications"],
                        aftercare_advice=payload["aftercare_advice"],
                    ))
                    try:
                        await save_sess.commit()
                        saved_count[0] += 1
                    except Exception as exc:
                        log.warning("save failed pid=%s lang=%s: %s", pid, lang, exc)
                        await save_sess.rollback()

            async def worker() -> None:
                while True:
                    try:
                        p, uk_t, lang = tasks_q.get_nowait()
                    except asyncio.QueueEmpty:
                        return
                    async with sem:
                        src = _to_dict(uk_t)
                        translated = await translate_one(client, args.model, p.name, src, lang)
                        if translated:
                            await save_translation(p.id, lang, translated)
                        done_count[0] += 1
                        if done_count[0] % 10 == 0 or done_count[0] == total:
                            log.info("  progress: %d / %d (saved=%d)",
                                     done_count[0], total, saved_count[0])

            workers = [asyncio.create_task(worker()) for _ in range(args.concurrency)]
            await asyncio.gather(*workers)
            log.info("DONE: saved %d / %d translations.", saved_count[0], total)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
