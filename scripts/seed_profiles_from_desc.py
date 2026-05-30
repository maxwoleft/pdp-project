"""Масове створення service profiles з файлу descServ.json.

Алгоритм:
1. Парсить descServ.json (408 рядків з описами послуг)
2. Для кожного service name обчислює canonical_key
3. Перевіряє чи є послуги з таким ключем в БД
4. Якщо ключ матчиться і профіль ще не створений → створює з описом
5. Для полів які LLM може заповнити — викликає GPT-4o-mini
6. Зберігає як enabled=False (draft) — для review

Запуск:
    python -m scripts.seed_profiles_from_desc                    # dry run
    python -m scripts.seed_profiles_from_desc --apply            # створити профілі
    python -m scripts.seed_profiles_from_desc --apply --with-ai  # + AI для keywords/problems
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

from app.adapters.embeddings.openai_embedder import OpenAIEmbedder
from app.domain.services.canonical_key import normalize_to_canonical_key, _ADDON_RE, _LENGTH_PATTERNS, _LEVEL_RE
from app.infrastructure.db.repositories.profile_repo import ServiceProfileRepository
from app.infrastructure.db.session import build_engine, build_session_factory

DESC_FILE = Path(__file__).parent.parent / "descServ.json"


def clean_service_name(name: str) -> str:
    """Чистимо від довжини/рівня/add-on для readable name."""
    clean = _ADDON_RE.sub(" ", name)
    for pat in _LENGTH_PATTERNS:
        clean = pat.sub(" ", clean)
    while True:
        new = _LEVEL_RE.sub("", clean).strip()
        if new == clean:
            break
        clean = new
    return re.sub(r"\s+", " ", clean).strip()


def parse_desc_file() -> list[dict]:
    """Парсить descServ.json → [{service, description, description2, all_keys, clean_name}, ...]"""
    items = []
    with open(DESC_FILE, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                print(f"WARN: line {line_no} — invalid JSON, skipping")
                continue
            service = obj.get("service", "").strip()
            desc = obj.get("description", "").strip()
            desc2 = obj.get("description2", "").strip()
            if not service or service == "Послуга":
                continue  # header row

            # Multi-variant: split by \n (деякі назви мультимовні через \n)
            variants = [v.strip() for v in service.split("\n") if v.strip()]
            all_keys: set[str] = set()
            for v in variants:
                key = normalize_to_canonical_key(v)
                if key:
                    all_keys.add(key)

            primary_key = normalize_to_canonical_key(variants[0]) if variants else ""
            clean_name = clean_service_name(variants[0]) if variants else service

            items.append({
                "original_name": service,
                "clean_name": clean_name,
                "primary_key": primary_key,
                "all_keys": all_keys,
                "description": desc.replace(";", ","),
                "description2": desc2.replace(";", ",") if desc2 else "",
            })
    return items


def fuzzy_match_db_key(
    desc_keys: set[str],
    db_keys: set[str],
) -> str | None:
    """Знаходить найкращий DB canonical_key для desc entry.

    3 стратегії (від точної до fuzzy):
    1. Exact match — desc_key == db_key
    2. Containment — desc_key є підрядком db_key або навпаки
    3. Token overlap ≥ 60% з мінімум 2 спільними токенами

    Повертає найкращий DB key або None.
    """
    # 1. Exact
    for dk in desc_keys:
        if dk in db_keys:
            return dk

    # 2. Containment (desc_key всередині db_key або навпаки)
    best_contain = None
    best_contain_len = 0
    for dk in desc_keys:
        for dbk in db_keys:
            if dk in dbk:
                if len(dk) > best_contain_len:
                    best_contain = dbk
                    best_contain_len = len(dk)
            elif dbk in dk:
                if len(dbk) > best_contain_len:
                    best_contain = dbk
                    best_contain_len = len(dbk)
    if best_contain and best_contain_len >= 8:  # мінімум 8 символів для containment
        return best_contain

    # 3. Token overlap ≥ 60%
    best_fuzzy = None
    best_fuzzy_score = 0.0
    for dk in desc_keys:
        dk_tokens = set(dk.split("_"))
        if len(dk_tokens) < 2:
            continue
        for dbk in db_keys:
            dbk_tokens = set(dbk.split("_"))
            if len(dbk_tokens) < 2:
                continue
            overlap = len(dk_tokens & dbk_tokens)
            total = max(len(dk_tokens), len(dbk_tokens))
            score = overlap / total
            if score >= 0.6 and overlap >= 2 and score > best_fuzzy_score:
                best_fuzzy = dbk
                best_fuzzy_score = score
    return best_fuzzy


async def generate_ai_fields(service_name: str, description: str, description2: str = "") -> dict:
    """Витягує structured поля з реального опису послуги через GPT-4o-mini.

    ВАЖЛИВО: AI не вигадує — він ВИТЯГУЄ з реального тексту опису.
    """
    from openai import AsyncOpenAI
    from app.core.config import get_settings

    settings = get_settings()
    # 60s per call + 1 retry — без таймауту запит може висіти годинами,
    # бо AsyncOpenAI default = 600s, чого достатньо щоб запороти весь run.
    client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=60.0, max_retries=1)

    full_desc = description
    if description2:
        full_desc += "\n\n" + description2

    prompt = f"""Ти — експерт beauty-індустрії. Тобі дано РЕАЛЬНИЙ детальний опис процедури салону краси.
ТІЛЬКИ на основі цього опису (не вигадуй нічого додатково) заповни structured поля для AI-агента.

Послуга: {service_name}

=== ОПИС ПРОЦЕДУРИ ===
{full_desc[:3000]}
=== КІНЕЦЬ ОПИСУ ===

На основі ТІЛЬКИ цього опису створи JSON з полями:

1. "short_description" — 1-2 речення: що це за послуга і для чого. Бери з першого абзацу опису.

2. "addresses_problems" — список 3-7 проблем/потреб клієнта які ця послуга вирішує.
   Витягуй з опису: якщо описано "для сухого волосся" → "сухість волосся".
   Якщо опис не містить конкретних проблем — виведи загальні зі змісту процедури.

3. "target_audience" — для кого підходить (2-4 типи клієнтів). З контексту опису.

4. "benefits" — 3-6 конкретних вигод. Бери ТІЛЬКИ те що реально написано в описі
   (наприклад "блиск", "захист структури", "покриття сивини 100%").

5. "keywords" — 8-15 слів/фраз якими клієнт може описати потребу в цій послузі.
   Включай: синоніми назви, сленг (шеллак=гель-лак), дієслова ("пофарбувати", "освіжити"),
   ключові слова з опису процедури. Обовʼязково українською.

6. "sales_pitch" — 1-2 речення готова рекомендація для AI-менеджера.
   Стиль: тепло, по-людськи, як живий адміністратор. Без emoji, без знаків оклику.
   Наприклад: "Раджу [послуга] — [причина]. Тримається [скільки]. Готова запропонувати час?"

7. "cross_sell" — 2-4 послуги які логічно запропонувати ПІСЛЯ цієї.
   Бери з опису якщо є рекомендації (наприклад "рекомендуємо тонування через 3-4 тижні").

8. "procedure_steps" — етапи процедури якщо описані в тексті. Коротко, 3-7 пунктів.

9. "contraindications" — протипоказання ЯКЩО чітко вказані в описі. Не вигадуй.
   Якщо опис не містить — пустий список.

10. "aftercare_advice" — поради по догляду після процедури ЯКЩО вказані в описі.
    Якщо ні — null.

Відповідь — ТІЛЬКИ валідний JSON, без markdown.
"""
    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_completion_tokens=800,
            temperature=0.2,
        )
        content = (resp.choices[0].message.content or "").strip()
        return json.loads(content)
    except Exception as exc:
        print(f"  AI ERROR for {service_name}: {exc}")
        return {}


async def amain():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Реально створити профілі")
    parser.add_argument("--with-ai", action="store_true", help="Витягти keywords/problems/benefits з опису через AI")
    parser.add_argument("--update", action="store_true", help="Оновити існуючі профілі")
    args = parser.parse_args()

    items = parse_desc_file()
    print(f"Parsed {len(items)} service descriptions from descServ.json")

    engine = build_engine()
    factory = build_session_factory(engine)
    embedder = OpenAIEmbedder()

    created = 0
    updated = 0
    skipped = 0
    no_match = 0

    try:
        async with factory() as session:
            repo = ServiceProfileRepository(session, embedder=embedder)

            # Збираємо всі canonical_keys з БД (один раз)
            from sqlalchemy import text as sql_text
            db_keys: set[str] = set()
            for c in ["ua", "pl", "gb"]:
                sql = sql_text(
                    f"SELECT DISTINCT canonical_key FROM {c}.service "
                    f"WHERE canonical_key IS NOT NULL AND archive = false"
                )
                for row in (await session.execute(sql)).fetchall():
                    db_keys.add(row[0])
            print(f"DB has {len(db_keys)} unique canonical_keys")

            for i, item in enumerate(items, 1):
                if not item["all_keys"]:
                    no_match += 1
                    continue

                # Fuzzy match — знаходимо найкращий DB key
                matched_key = fuzzy_match_db_key(item["all_keys"], db_keys)
                if not matched_key:
                    no_match += 1
                    continue

                # Скільки послуг покриває цей ключ
                svc_count = await repo.count_services_for_canonical_key(matched_key)
                if svc_count == 0:
                    no_match += 1
                    continue

                existing = await repo.get_by_canonical_key(matched_key)
                if existing and not args.update:
                    skipped += 1
                    continue

                if not args.apply:
                    match_type = "exact" if matched_key in item["all_keys"] else "fuzzy"
                    print(f"  [{match_type:5s}] {matched_key:50s} → {svc_count:4d} svc → '{item['clean_name']}'")
                    created += 1
                    continue

                # Формуємо описи
                desc = item["description"]
                desc2 = item["description2"]
                first_sentence = desc.split("\n")[0][:500]

                # AI fields — витягує з реального опису
                ai_fields = {}
                if args.with_ai:
                    ai_fields = await generate_ai_fields(
                        item["clean_name"], desc, desc2
                    )
                    if ai_fields:
                        # AI може повернути short_description кращий ніж перше речення
                        ai_short = ai_fields.get("short_description")
                        if ai_short and len(ai_short) > 10:
                            first_sentence = ai_short
                        # aftercare_advice у БД — VARCHAR/Text. Інколи модель повертає
                        # список — склеюємо в один текст через перенос рядка.
                        ac = ai_fields.get("aftercare_advice")
                        if isinstance(ac, list):
                            ai_fields["aftercare_advice"] = (
                                "\n".join(str(x).strip() for x in ac if x) or None
                            )

                if existing:
                    await repo.upsert_translation(
                        existing.id,
                        "uk",
                        short_description=first_sentence,
                        detailed_description=desc + ("\n\n" + desc2 if desc2 else ""),
                        addresses_problems=ai_fields.get("addresses_problems", []),
                        target_audience=ai_fields.get("target_audience", []),
                        benefits=ai_fields.get("benefits", []),
                        keywords=ai_fields.get("keywords", []),
                        sales_pitch=ai_fields.get("sales_pitch"),
                        cross_sell=ai_fields.get("cross_sell", []),
                        procedure_steps=ai_fields.get("procedure_steps", []),
                        contraindications=ai_fields.get("contraindications", []),
                        aftercare_advice=ai_fields.get("aftercare_advice"),
                    )
                    await repo.update_fields(existing.id, name=item["clean_name"], updated_by="desc_seed")
                    await repo.save_version(existing.id,
                                            change_summary="Updated from descServ.json + AI extraction",
                                            created_by="desc_seed")
                    updated += 1
                    print(f"  [{i}/{len(items)}] UPDATE {matched_key}", flush=True)
                else:
                    profile = await repo.create(
                        canonical_key=matched_key,
                        name=item["clean_name"],
                        country=None,
                        default_language="uk",
                        enabled=True,
                        created_by="desc_seed",
                        updated_by="desc_seed",
                    )
                    await repo.upsert_translation(
                        profile.id,
                        "uk",
                        short_description=first_sentence,
                        detailed_description=desc + ("\n\n" + desc2 if desc2 else ""),
                        addresses_problems=ai_fields.get("addresses_problems", []),
                        target_audience=ai_fields.get("target_audience", []),
                        benefits=ai_fields.get("benefits", []),
                        keywords=ai_fields.get("keywords", []),
                        sales_pitch=ai_fields.get("sales_pitch"),
                        cross_sell=ai_fields.get("cross_sell", []),
                        procedure_steps=ai_fields.get("procedure_steps", []),
                        contraindications=ai_fields.get("contraindications", []),
                        aftercare_advice=ai_fields.get("aftercare_advice"),
                    )
                    await repo.save_version(profile.id,
                                            change_summary="Created from descServ.json + AI extraction",
                                            created_by="desc_seed")
                    created += 1
                    print(f"  [{i}/{len(items)}] CREATE {matched_key} → {svc_count} svc", flush=True)

                # Per-batch commit раз на 20 записів — щоб не втратити прогрес,
                # якщо OpenAI повисне або скрипт упаде на середині.
                if args.apply and (created + updated) > 0 and (created + updated) % 20 == 0:
                    await session.commit()
                    print(f"  ─── commit @ {created + updated} done ───", flush=True)

            if args.apply:
                await session.commit()
    finally:
        await engine.dispose()

    print(f"\nDONE: {created} created, {updated} updated, {skipped} skipped, {no_match} no match in DB")
    if not args.apply:
        print("(DRY RUN — use --apply to create profiles)")


if __name__ == "__main__":
    asyncio.run(amain())
