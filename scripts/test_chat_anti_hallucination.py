"""Phase 5: Anti-hallucination chat tests.

Прогонює серію scenarios через /api/chat/send. Перевіряє:
  POSITIVE — query про реальну послугу → response містить очікувані ключові слова.
  NEGATIVE — query про вигадану послугу → response чесно каже "немає".

Запуск:
    python -m scripts.test_chat_anti_hallucination
"""
from __future__ import annotations

import asyncio
import json
import sys

import httpx

BASE_URL = "https://ai-chat.p-de-p.com"

# Salon UUIDs (з salon_registry)
UA_KYIV_OBOLON = "fc2968cc-8131-5466-8960-6094b78a0e8f"
GB_OXFORD_CIRCUS = "72b6595b-622b-51f8-becd-c1f8f9545ce1"


SCENARIOS = [
    # POSITIVE — реальні послуги
    {
        "name": "UA фарбування коренів",
        "country": "ua",
        "salon_id": UA_KYIV_OBOLON,
        "query": "Хочу пофарбувати тільки корені, не всю довжину",
        "expect_any": ["корен", "корін", "фарбуван"],
        "forbid": [],
    },
    {
        "name": "UA airtouch висвітлення",
        "country": "ua",
        "salon_id": UA_KYIV_OBOLON,
        "query": "Чи робите Airtouch?",
        "expect_any": ["airtouch", "ейртач", "повітрян"],
        "forbid": [],
    },
    {
        "name": "UA масаж стоп",
        "country": "ua",
        "salon_id": UA_KYIV_OBOLON,
        "query": "Хочу масаж стоп",
        "expect_any": ["масаж", "стоп"],
        "forbid": [],
    },
    {
        "name": "UA проти випадання волосся",
        "country": "ua",
        "salon_id": UA_KYIV_OBOLON,
        "query": "Що порадите проти випадання волосся?",
        "expect_any": ["лікуван", "відновлен", "догляд", "волос"],
        "forbid": [],
    },
    {
        "name": "GB педикюр",
        "country": "gb",
        "salon_id": GB_OXFORD_CIRCUS,
        "query": "Чи робите ви педикюр?",
        "expect_any": ["педикюр", "pedicure"],
        "forbid": [],
    },
    # NEGATIVE — вигадані послуги (не існують у DB)
    {
        "name": "UA вигадане тату-видалення",
        "country": "ua",
        "salon_id": UA_KYIV_OBOLON,
        "query": "У вас є видалення татуювань лазером?",
        "expect_any": ["немає", "не пропонує", "на жаль", "не маємо", "не робимо"],
        "forbid": [],
    },
    {
        "name": "GB вигадане акупунктура",
        "country": "gb",
        "salon_id": GB_OXFORD_CIRCUS,
        "query": "Чи є у вас акупунктура?",
        "expect_any": ["немає", "не пропонує", "на жаль", "не маємо", "не робимо"],
        "forbid": [],
    },
]


async def onboard(client: httpx.AsyncClient, country: str, salon_id: str) -> None:
    """Set country + salon у сесії."""
    r = await client.post(f"{BASE_URL}/api/chat/country", data={"country": country})
    r.raise_for_status()
    r = await client.post(f"{BASE_URL}/api/chat/salon", data={"salon_id": salon_id})
    r.raise_for_status()


async def reset_session(client: httpx.AsyncClient) -> None:
    """Reset / restart chat для clean state."""
    try:
        await client.get(f"{BASE_URL}/chat/restart")
    except Exception:
        pass


async def send_message(client: httpx.AsyncClient, text: str) -> str:
    r = await client.post(f"{BASE_URL}/api/chat/send", data={"text": text})
    r.raise_for_status()
    data = r.json()
    return data.get("reply", "")


async def run_scenario(scn: dict) -> dict:
    print(f"\n=== {scn['name']} ===")
    print(f"Q: {scn['query']}")
    async with httpx.AsyncClient(timeout=120.0, cookies={}) as client:
        await reset_session(client)
        await onboard(client, scn["country"], scn["salon_id"])
        try:
            reply = await send_message(client, scn["query"])
        except Exception as exc:
            return {"name": scn["name"], "passed": False, "error": str(exc)}

    reply_lower = (reply or "").lower()
    print(f"A: {reply[:600]}{'...' if len(reply) > 600 else ''}")

    expected_hit = any(e.lower() in reply_lower for e in scn["expect_any"])
    forbidden_hit = any(f.lower() in reply_lower for f in scn.get("forbid", []))

    passed = expected_hit and not forbidden_hit
    return {
        "name": scn["name"],
        "passed": passed,
        "reply": reply,
        "expected_any": scn["expect_any"],
        "expected_hit": expected_hit,
        "forbidden_hit": forbidden_hit,
    }


async def main() -> None:
    results = []
    for scn in SCENARIOS:
        try:
            res = await run_scenario(scn)
        except Exception as exc:
            res = {"name": scn["name"], "passed": False, "error": str(exc)}
        results.append(res)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    passed = sum(1 for r in results if r.get("passed"))
    failed = len(results) - passed
    for r in results:
        status = "✓ PASS" if r.get("passed") else "✗ FAIL"
        print(f"  {status}  {r['name']}")
        if not r.get("passed"):
            if "error" in r:
                print(f"         ERROR: {r['error']}")
            else:
                print(f"         expected_hit={r.get('expected_hit')} forbidden_hit={r.get('forbidden_hit')}")
    print(f"\nTotal: {passed}/{len(results)} passed")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
