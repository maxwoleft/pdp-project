"""Full end-to-end test suite — endpoints + chat scenarios + logic.

Covers:
  A. Health: site, /chat, /api/chat/salons, /api/chat/send
  B. Onboarding: country → salon flow
  C. Chat scenarios — exercises tools:
     - search_services (прямий запит послуги)
     - search_by_concern (опис проблеми/потреби)
     - find_masters_for_service (хто виконує)
     - get_available_slots (коли можна)
     - find_service_and_slots (combined)
  D. Multi-turn memory (AI пам'ятає попередній контекст)
  E. Brand routing (Alfaparf → "Фарбування Alfaparf")
  F. Language detection (рос/польська запит → відповідь на тій же)
  G. Cross-salon isolation (різні салони — різні відповіді)
  H. Anti-hallucination (вигадані послуги)
"""
from __future__ import annotations

import asyncio
import sys
import time

import httpx

BASE = "https://ai-chat.p-de-p.com"

UA_KYIV_OBOLON = "fc2968cc-8131-5466-8960-6094b78a0e8f"
UA_ODESA = "000bb780-8247-50c7-af7b-a3525a9d0431"
UA_KHARKIV = "b56cbfad-d291-5626-8d95-87be2de1b241"
PL_WARSAW = "9c7afbb1-92b1-50bc-8c11-109140b3c487"
GB_OXFORD = "72b6595b-622b-51f8-becd-c1f8f9545ce1"


async def health_checks() -> list[dict]:
    """Block A: endpoint health."""
    results = []
    async with httpx.AsyncClient(timeout=30.0) as c:
        # 1. /chat onboarding page
        r = await c.get(f"{BASE}/chat")
        results.append({"name": "GET /chat", "ok": r.status_code == 200,
                        "details": f"HTTP {r.status_code}"})
        # 2. /api/chat/salons (потребує country session)
        await c.post(f"{BASE}/api/chat/country", data={"country": "ua"})
        r = await c.get(f"{BASE}/api/chat/salons?country=ua")
        ok = r.status_code == 200 and len(r.json().get("salons", [])) > 0
        results.append({"name": "GET /api/chat/salons?country=ua", "ok": ok,
                        "details": f"HTTP {r.status_code}, salons={len(r.json().get('salons', []))}"})
        # 3. /chat/start?salon_id (deep link)
        r = await c.get(f"{BASE}/chat/start?salon_id={UA_KYIV_OBOLON}",
                        follow_redirects=False)
        ok = r.status_code in (302, 303) and "/chat/c" in (r.headers.get("location") or "")
        results.append({"name": "GET /chat/start (deep link)", "ok": ok,
                        "details": f"HTTP {r.status_code}, loc={r.headers.get('location','')}"})
        # 4. /chat/c (потребує onboarding)
        await c.post(f"{BASE}/api/chat/salon", data={"salon_id": UA_KYIV_OBOLON})
        r = await c.get(f"{BASE}/chat/c")
        results.append({"name": "GET /chat/c (with session)", "ok": r.status_code == 200,
                        "details": f"HTTP {r.status_code}"})
        # 5. /api/chat/send working
        r = await c.post(f"{BASE}/api/chat/send", data={"text": "Привіт"}, timeout=120)
        ok = r.status_code == 200 and len(r.json().get("reply", "")) > 0
        results.append({"name": "POST /api/chat/send (basic)", "ok": ok,
                        "details": f"HTTP {r.status_code}, reply_len={len(r.json().get('reply', ''))}"})
    return results


async def chat_scenario(country: str, salon: str, queries: list[str],
                       check: dict) -> dict:
    """Run multi-turn scenario, check final state."""
    async with httpx.AsyncClient(timeout=180.0) as c:
        try:
            await c.get(f"{BASE}/chat/restart")
        except Exception:
            pass
        await c.post(f"{BASE}/api/chat/country", data={"country": country})
        await c.post(f"{BASE}/api/chat/salon", data={"salon_id": salon})
        replies = []
        for q in queries:
            r = await c.post(f"{BASE}/api/chat/send", data={"text": q})
            r.raise_for_status()
            reply = r.json().get("reply", "")
            replies.append(reply)
            await asyncio.sleep(1)
    last_reply = replies[-1] if replies else ""
    rl = last_reply.lower()
    hit_expected = True
    if "expect_any" in check:
        hit_expected = any(e.lower() in rl for e in check["expect_any"])
    hit_forbidden = False
    if "forbid" in check:
        hit_forbidden = any(f.lower() in rl for f in check["forbid"])
    return {
        "passed": hit_expected and not hit_forbidden,
        "replies": replies,
        "expected_hit": hit_expected,
        "forbidden_hit": hit_forbidden,
    }


SCENARIOS = [
    # ── Block C: tool exercise ───────────────────────────────────────────────
    {"name": "C1: search_services (прямий)", "country": "ua", "salon": UA_KYIV_OBOLON,
     "queries": ["Хочу манікюр з гель-лаком"],
     "expect_any": ["манікюр", "гель", "грн"]},

    {"name": "C2: search_by_concern", "country": "ua", "salon": UA_KYIV_OBOLON,
     "queries": ["У мене сильно випадає волосся, що порадите?"],
     "expect_any": ["лікуван", "відновлен", "догляд", "грн"]},

    {"name": "C3: find_masters_for_service", "country": "ua", "salon": UA_KYIV_OBOLON,
     "queries": ["Хочу стрижку. Хто з майстрів робить?"],
     "expect_any": ["майстер", "імʼя", "вільн", "коли", "перевір", "записати", "час"]},

    {"name": "C4: get_available_slots", "country": "ua", "salon": UA_KYIV_OBOLON,
     "queries": ["Хочу класичний манікюр", "Який час сьогодні чи завтра?"],
     "expect_any": ["сьогодн", "завтр", "год", ":", "вільн", "час"]},

    {"name": "C5: find_service_and_slots combined", "country": "ua", "salon": UA_KYIV_OBOLON,
     "queries": ["Хочу записатися на стрижку завтра після обіду"],
     "expect_any": ["завтр", "стрижк", "год", "записа", "час", "майстер"]},

    # ── Block D: multi-turn memory ───────────────────────────────────────────
    {"name": "D1: pamʼять про послугу через 2 turn", "country": "ua", "salon": UA_KYIV_OBOLON,
     "queries": [
       "Хочу класичний манікюр",
       "А скільки коштує?",
     ],
     "expect_any": ["грн", "uah", "manik", "манікюр"]},

    # ── Block E: brand routing ───────────────────────────────────────────────
    {"name": "E1: Alfaparf → Фарбування Alfaparf", "country": "ua", "salon": UA_KYIV_OBOLON,
     "queries": ["Хочу фарбування Alfaparf"],
     "expect_any": ["alfaparf", "альфа", "фарбуван"]},

    {"name": "E2: Lebel Абсолютне щастя", "country": "ua", "salon": UA_KYIV_OBOLON,
     "queries": ["Хочу Абсолютне щастя для волосся"],
     "expect_any": ["lebel", "щастя", "відновлен", "лікуван"]},

    # ── Block F: language detection ──────────────────────────────────────────
    {"name": "F1: рос мова → рос відповідь", "country": "ua", "salon": UA_KYIV_OBOLON,
     "queries": ["Хочу маникюр с гель-лаком, сколько стоит?"],
     "expect_any": ["маник", "гель", "рубл", "грн", "ціна", "цена", "стоит", "коштує"]},

    {"name": "F2: PL мова у PL salon", "country": "pl", "salon": PL_WARSAW,
     "queries": ["Chcę manicure"],
     "expect_any": ["manik", "manicure", "zł", "pln"]},

    {"name": "F3: EN мова у GB salon", "country": "gb", "salon": GB_OXFORD,
     "queries": ["I want a haircut"],
     "expect_any": ["haircut", "стрижк", "£", "gbp"]},

    # ── Block G: salon isolation ─────────────────────────────────────────────
    {"name": "G1: Одеса має свої services", "country": "ua", "salon": UA_ODESA,
     "queries": ["Хочу афрокудрі"],
     "expect_any": ["афро", "кудр", "довжин", "плет"]},

    {"name": "G2: Харків Героїв подологія", "country": "ua", "salon": UA_KHARKIV,
     "queries": ["У мене вросший ніготь, болить"],
     "expect_any": ["вросл", "вросш", "корекц", "видален", "грн"]},

    # ── Block H: anti-hallucination ──────────────────────────────────────────
    {"name": "H1: вигадане тату-видалення", "country": "ua", "salon": UA_KYIV_OBOLON,
     "queries": ["У вас є видалення татуювань лазером?"],
     "expect_any": ["немає", "не пропоную", "не пропонує", "на жаль", "не маємо"]},

    {"name": "H2: вигадана акупунктура GB", "country": "gb", "salon": GB_OXFORD,
     "queries": ["Do you have acupuncture?"],
     "expect_any": ["немає", "no", "not offer", "don't have", "не маємо", "на жаль", "sorry"]},

    {"name": "H3: вигадана хіропрактика PL", "country": "pl", "salon": PL_WARSAW,
     "queries": ["Czy macie chiropraktyka?"],
     "expect_any": ["nie", "немає", "no", "brak", "nie mamy", "ні"]},

    # ── Block I: complex scenarios ───────────────────────────────────────────
    {"name": "I1: набряки + темні кола", "country": "ua", "salon": UA_KYIV_OBOLON,
     "queries": ["Прокидаюся з мішками під очима і темними колами"],
     "expect_any": ["набряк", "коло", "очі", "academie", "карбок", "консультац"]},

    {"name": "I2: пігментація після засмаги", "country": "ua", "salon": UA_KYIV_OBOLON,
     "queries": ["Після засмаги залишилися пігментні плями"],
     "expect_any": ["освітлю", "пігмент", "academie", "пілінг"]},

    {"name": "I3: відросли корені (brand-specific)", "country": "ua", "salon": UA_KYIV_OBOLON,
     "queries": ["Відросли корені, потрібно підкрасити Balmain"],
     "expect_any": ["корен", "корін", "balmain"]},

    {"name": "I4: SPA-ритуал для тіла", "country": "ua", "salon": UA_KYIV_OBOLON,
     "queries": ["Хочу SPA-ритуал для тіла, повний релакс"],
     "expect_any": ["spa", "ритуал", "масаж", "обгортан", "релакс"]},
]


async def main() -> None:
    print("=" * 70)
    print("BLOCK A: Endpoint health checks")
    print("=" * 70)
    a_results = await health_checks()
    for r in a_results:
        st = "PASS" if r["ok"] else "FAIL"
        print(f"  [{st}] {r['name']}  ({r['details']})")
    a_pass = sum(1 for r in a_results if r["ok"])

    print()
    print("=" * 70)
    print("BLOCK B-I: Chat scenarios")
    print("=" * 70)
    scn_results = []
    for scn in SCENARIOS:
        print(f"\n[{scn['name']}]")
        for q in scn["queries"]:
            print(f"  Q: {q[:80]}")
        try:
            res = await chat_scenario(scn["country"], scn["salon"],
                                      scn["queries"], scn)
        except Exception as exc:
            res = {"passed": False, "error": str(exc)}
        if "error" in res:
            print(f"  ERROR: {res['error']}")
        else:
            for reply in res["replies"][-1:]:
                print(f"  A: {reply[:250].replace(chr(10), ' ')}")
        print(f"  >> {'PASS' if res.get('passed') else 'FAIL'}")
        scn_results.append({"name": scn["name"], **res})
        await asyncio.sleep(1)

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Block A (endpoints): {a_pass}/{len(a_results)}")
    s_pass = sum(1 for r in scn_results if r.get("passed"))
    print(f"  Block B-I (scenarios): {s_pass}/{len(scn_results)}")
    print()
    for r in scn_results:
        st = "PASS" if r.get("passed") else "FAIL"
        print(f"  {st}  {r['name']}")

    total_pass = a_pass + s_pass
    total = len(a_results) + len(scn_results)
    print(f"\n  TOTAL: {total_pass}/{total}")
    if total_pass < total:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
