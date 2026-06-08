"""Smoke test runner — швидкий regression тест після зміни system prompt.

6 критичних scenarios, ~60 секунд total. Викликається у background після
save settings. Результати у public.instruction_test_run.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

import httpx
from sqlalchemy import text

log = logging.getLogger("smoke_test")

BASE = "https://ai-chat.p-de-p.com"

UA_KYIV = "fc2968cc-8131-5466-8960-6094b78a0e8f"
GB_OXFORD = "72b6595b-622b-51f8-becd-c1f8f9545ce1"

SMOKE_SCENARIOS = [
    # 1. Клієнт знає конкретну послугу і хоче записатись
    {"id": "1_known_service_book", "country": "ua", "salon": UA_KYIV,
     "query": "Хочу записатись на класичний манікюр",
     "expect_any": ["манікюр", "записа", "так", "коли", "довжин", "майстер", "час"]},

    # 2. Клієнт не знає, яку послугу обрати
    {"id": "2_dont_know_service", "country": "ua", "salon": UA_KYIV,
     "query": "Не знаю що мені обрати, що порадите?",
     "expect_any": ["що", "?", "потреб", "ціль", "опиш", "хоч", "цікав", "запит", "розкаж", "розповід"]},

    # 3. Клієнт хоче найближчий доступний час
    {"id": "3_nearest_slot", "country": "ua", "salon": UA_KYIV,
     "query": "Коли є найближче вільне місце?",
     "expect_any": ["яку", "послуг", "що", "?", "сьогодн", "завтр", "час", "вільн"]},

    # 4. Клієнт хоче конкретного майстра
    {"id": "4_specific_master", "country": "ua", "salon": UA_KYIV,
     "query": "Хочу записатися саме до Анни",
     "expect_any": ["анн", "перевір", "майстер", "поглян", "яку", "послуг", "хто", "перевірю", "немає", "немаЕ"]},

    # 5. Клієнт уточнює конкретний салон
    {"id": "5_specific_salon", "country": "ua", "salon": UA_KYIV,
     "query": "Чи цей салон знаходиться у Києві на Оболоні?",
     "expect_any": ["так", "оболонь", "обол", "київ", "адрес", "знаход", "вірно"]},

    # 6. Клієнт питає ціну
    {"id": "6_price_question", "country": "ua", "salon": UA_KYIV,
     "query": "Скільки коштує жіноча стрижка?",
     "expect_any": ["грн", "стрижк", "коштує", "ціна", "вартість"]},

    # 7. Клієнт описує проблему, але не знає назву послуги
    {"id": "7_concern_no_service", "country": "ua", "salon": UA_KYIV,
     "query": "У мене дуже сильно випадає волосся, що робити?",
     "expect_any": ["лікуван", "відновлен", "догляд", "волос", "грн"]},

    # 8. Клієнту потрібна складна консультація
    {"id": "8_complex_consultation", "country": "ua", "salon": UA_KYIV,
     "query": "Хочу повністю змінити імідж: колір волосся, стрижку і доглянути нігті — підкажіть план",
     "expect_any": ["колір", "фарбуван", "стрижк", "манікюр", "догляд", "пропоную", "почн", "почати"]},

    # 9. Клієнт хоче скасувати запис
    {"id": "9_cancel_booking", "country": "ua", "salon": UA_KYIV,
     "query": "Хочу скасувати свій запис",
     "expect_any": ["скасу", "телефон", "перевір", "номер", "запис", "підтверд", "коли", "час"]},
]


async def _run_one(scn: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=120.0) as c:
            try:
                await c.get(f"{BASE}/chat/restart")
            except Exception:
                pass
            await c.post(f"{BASE}/api/chat/country", data={"country": scn["country"]})
            await c.post(f"{BASE}/api/chat/salon", data={"salon_id": scn["salon"]})
            r = await c.post(f"{BASE}/api/chat/send", data={"text": scn["query"]})
            r.raise_for_status()
            reply = r.json().get("reply", "")
    except Exception as exc:
        return {"id": scn["id"], "passed": False, "error": str(exc), "reply": ""}

    rl = reply.lower()
    hit = any(e.lower() in rl for e in scn["expect_any"])
    return {"id": scn["id"], "passed": hit, "reply": reply[:300],
            "query": scn["query"]}


async def run_smoke_async(
    session_factory, run_id: str, triggered_by_path: str | None, user_email: str | None,
) -> None:
    """Background task — виконати всі scenarios + оновити DB row."""
    results: list[dict] = []
    for scn in SMOKE_SCENARIOS:
        try:
            res = await _run_one(scn)
        except Exception as exc:
            res = {"id": scn["id"], "passed": False, "error": str(exc)}
        results.append(res)
        await asyncio.sleep(1)

    passed = sum(1 for r in results if r.get("passed"))
    total = len(results)
    status = "passed" if passed == total else "failed"

    import json
    try:
        async with session_factory() as session:
            await session.execute(text("""
                UPDATE public.instruction_test_run
                SET status = :status, passed = :p, total = :t,
                    details = CAST(:d AS jsonb), finished_at = now()
                WHERE id = :id
            """), {
                "status": status, "p": passed, "t": total,
                "d": json.dumps(results, ensure_ascii=False),
                "id": run_id,
            })
            await session.commit()
        log.info("smoke test %s done: %d/%d", run_id, passed, total)
    except Exception as exc:
        log.warning("smoke test result save failed: %s", exc)


async def trigger_smoke_test(
    session_factory, triggered_by_path: str | None, user_email: str | None,
) -> str:
    """Insert row + spawn background task. Returns run_id."""
    run_id = str(uuid.uuid4())
    async with session_factory() as session:
        await session.execute(text("""
            INSERT INTO public.instruction_test_run (id, triggered_by_path, triggered_by_user, status)
            VALUES (:id, :p, :u, 'running')
        """), {"id": run_id, "p": triggered_by_path, "u": user_email})
        await session.commit()
    asyncio.create_task(
        run_smoke_async(session_factory, run_id, triggered_by_path, user_email)
    )
    return run_id
