"""LLM-based генерація key_descriptions для нових canonical_keys.
Викликається cron'ом після auto_link_missing_keys.
Замінює rule-based descriptions експертними intent-detection описами через OpenAI.

LLM виступає як beauty industry expert + AI conversation designer.
Генерує контекст у форматі intent-detection: 'Пропонувати коли клієнт ...'.

Запуск:
    python -m scripts.llm_fill_key_descriptions --country gb --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re

from openai import AsyncOpenAI
from sqlalchemy import text

from app.core.config import get_settings
from app.infrastructure.db.session import build_engine, build_session_factory

log = logging.getLogger("llm_key_desc")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


SYSTEM_PROMPT = (
    "You are a beauty industry expert with 20+ years in salon services AND an AI conversation designer. "
    "Your task: write intent-detection context for AI sales chatbot. "
    "The AI must DETECT CLIENT NEED and propose the right service — not list menus.\n\n"
    "Context format: 1-2 sentences in Ukrainian. Start with 'Пропонувати коли клієнт виявляє потребу [...]'. "
    "Then add: 'Виявити через: [specific conversation signals — words, situations, pain points]'.\n\n"
    "Style: Master-Sensei voice, formal «ви», no emoji, no exclamation marks. "
    "Be precise: WHAT need this specific service solves, HOW to spot it in conversation. "
    "Don't write 'клієнт обрав' — chat detects need, doesn't list choices."
)


def extract_ua_name(sample: str) -> str:
    if not sample:
        return ""
    parts = [p.strip() for p in sample.split("/")]
    for p in parts:
        if p.startswith("UA "):
            return p[3:].strip()
    return sample.strip()


def is_auto_generated(desc: str) -> bool:
    """Heuristic: auto-generated descriptions починаються з шаблону."""
    if not desc:
        return True
    markers = [
        "Пропонувати коли клієнт виявляє потребу у", "Стандартна послуга:",
        "Комплексна послуга:", "Сервіс типу:",
    ]
    return any(desc.startswith(m) for m in markers)


async def generate_context(
    client: AsyncOpenAI, model: str, profile_name: str, ua_sample: str
) -> str | None:
    user_prompt = (
        f"Profile: '{profile_name}'\n"
        f"Сервіс назва (UA): '{ua_sample}'\n\n"
        f"Напишіть intent-detection контекст для AI чатбота. Формат: "
        f"'Пропонувати коли клієнт виявляє потребу [конкретну]. Виявити через: [сигнали].'"
    )
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_completion_tokens=300,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        log.warning("LLM call failed: %s", exc)
        return None


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", required=True, choices=["ua", "pl", "gb"])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--model", default="gpt-5-mini")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    settings = get_settings()
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            # Знаходимо keys з auto-generated descriptions АБО без description
            r = await session.execute(text(f"""
                SELECT p.id, p.name AS profile_name, k.key, k.val AS curr_desc,
                       (SELECT name FROM {args.country}.service s
                        WHERE s.archive=false AND s.canonical_key = k.key
                        ORDER BY s.name LIMIT 1) AS sample_name
                FROM public.service_profile p
                CROSS JOIN LATERAL jsonb_array_elements_text(p.canonical_keys) AS canonical_key(key)
                LEFT JOIN LATERAL jsonb_each_text(COALESCE(p.key_descriptions, '{{}}'::jsonb)) AS k(key, val)
                  ON k.key = canonical_key.key
                WHERE p.country = :c
                  AND EXISTS (SELECT 1 FROM {args.country}.service s2
                              WHERE s2.archive=false AND s2.canonical_key = canonical_key.key)
            """), {"c": args.country})
            rows = r.all()

            todo: list[tuple[str, str, str, str]] = []
            for pid, pname, ck, curr_desc, sample in rows:
                if curr_desc and not is_auto_generated(curr_desc):
                    continue  # expert-written — skip
                ua_sample = extract_ua_name(sample or "")
                if not ua_sample:
                    continue
                todo.append((str(pid), pname, ck, ua_sample))

            if args.limit:
                todo = todo[: args.limit]

            log.info("Keys to fill: %d", len(todo))
            if not args.apply:
                for pid, pname, ck, ua in todo[:10]:
                    log.info("  %s | %s | %s", pname, ck[:30], ua[:60])
                log.info("DRY RUN")
                return

            sem = asyncio.Semaphore(args.concurrency)
            done = [0]

            async def process_one(pid: str, pname: str, ck: str, ua: str) -> None:
                async with sem:
                    desc = await generate_context(client, args.model, pname, ua)
                    if desc:
                        await session.execute(text("""
                            UPDATE public.service_profile
                            SET key_descriptions = COALESCE(key_descriptions, '{}'::jsonb)
                                || CAST(:dj AS jsonb)
                            WHERE id = :pid
                        """), {"dj": json.dumps({ck: desc}), "pid": pid})
                    done[0] += 1
                    if done[0] % 10 == 0:
                        log.info("  progress: %d / %d", done[0], len(todo))

            await asyncio.gather(*[process_one(*item) for item in todo])
            await session.commit()
            log.info("DONE: %d filled", done[0])
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
