"""20-scenario anti-hallucination chat test across UA/PL/GB salons.

POSITIVE — клієнт описує потребу → AI підбирає specific service.
NEGATIVE — вигадана послуга → AI чесно "немає".
"""
from __future__ import annotations

import asyncio
import sys

import httpx

BASE = "https://ai-chat.p-de-p.com"

# Salons
UA_KYIV_OBOLON = "fc2968cc-8131-5466-8960-6094b78a0e8f"
UA_KYIV_BESARABKA = "3a25518e-4d32-5ebf-94ea-fbf8282bc672"
UA_ODESA = "000bb780-8247-50c7-af7b-a3525a9d0431"
UA_KHARKIV_HEROIV = "b56cbfad-d291-5626-8d95-87be2de1b241"
UA_KHARKIV_NAUKY = "0d8207b7-1634-51db-af4e-6408df3fa89b"
UA_UZHHOROD = "6a8ca78e-d63a-5976-86f7-7a7aaad5f039"
UA_BUCHA = "8fb34cf2-175a-5366-ad2f-b563b898b02e"
PL_WARSAW_GRZYBOWSKA = "9c7afbb1-92b1-50bc-8c11-109140b3c487"
PL_WROCLAW = "d773f2a9-9ffd-5cac-932d-ad4e94d93839"
GB_OXFORD = "72b6595b-622b-51f8-becd-c1f8f9545ce1"
GB_BROMPTON = "547d7d8a-286f-5169-9759-f27d2efc7d84"

SCENARIOS = [
    {"id": 1, "country": "ua", "salon": UA_KYIV_OBOLON,
     "query": "У мене після засмаги залишилися пігментні плями на обличчі. Що порадите?",
     "expect_any": ["освітлю", "пігмент", "academie"]},
    {"id": 2, "country": "ua", "salon": UA_KYIV_OBOLON,
     "query": "Хочу пофарбувати лише корені, не всю довжину",
     "expect_any": ["корен", "корін"]},
    {"id": 3, "country": "ua", "salon": UA_KYIV_OBOLON,
     "query": "Робите Airtouch висвітлення?",
     "expect_any": ["airtouch", "ейртач", "повітрян"]},
    {"id": 4, "country": "ua", "salon": UA_KYIV_OBOLON,
     "query": "Волосся сильно пошкоджене після освітлення, потрібна реанімація",
     "expect_any": ["лікуван", "відновлен", "щастя", "lebel"]},
    {"id": 5, "country": "ua", "salon": UA_KYIV_OBOLON,
     "query": "У мене тонкі ламкі нігті, постійно ламаються",
     "expect_any": ["зміцнен", "японськ", "ibx", "догляд"]},
    {"id": 6, "country": "ua", "salon": UA_KYIV_OBOLON,
     "query": "У мене весілля через місяць, потрібен макіяж і зачіска",
     "expect_any": ["весіл", "макіяж", "зачіск"]},
    {"id": 7, "country": "ua", "salon": UA_KYIV_OBOLON,
     "query": "Прокидаюся з набряками і темними колами під очима",
     "expect_any": ["набряк", "коло", "очі", "academie", "карбок"]},
    {"id": 8, "country": "ua", "salon": UA_KYIV_OBOLON,
     "query": "Болить шия від роботи за компʼютером",
     "expect_any": ["шия", "шийн", "масаж", "комір", "швз"]},
    {"id": 9, "country": "ua", "salon": UA_KYIV_OBOLON,
     "query": "Хочу позбутися целюліту на стегнах",
     "expect_any": ["целюліт", "endosphere", "lpg", "обгортан", "лімфодренаж"]},
    {"id": 10, "country": "ua", "salon": UA_KHARKIV_HEROIV,
     "query": "У мене вросший ніготь, болить",
     "expect_any": ["вросш", "врослий", "онихокрипт", "подолог"]},
    {"id": 11, "country": "ua", "salon": UA_ODESA,
     "query": "Хочу афрокудрі на фотосесію",
     "expect_any": ["афрокудр", "афро"]},
    {"id": 12, "country": "ua", "salon": UA_KHARKIV_NAUKY,
     "query": "Потрібна чоловіча стрижка",
     "expect_any": ["чоловіч", "стрижк"]},
    {"id": 13, "country": "ua", "salon": UA_UZHHOROD,
     "query": "Хочу доглянуту бороду і вуса",
     "expect_any": ["борода", "вуса", "грумінг"]},
    {"id": 14, "country": "ua", "salon": UA_BUCHA,
     "query": "Хочу нарощування вій 3D",
     "expect_any": ["вії", "нарощуван", "3d"]},
    {"id": 15, "country": "pl", "salon": PL_WARSAW_GRZYBOWSKA,
     "query": "Маю кучеряве волосся, набридло щодня випрямляти",
     "expect_any": ["керати", "випрямлен", "ботокс"]},
    {"id": 16, "country": "pl", "salon": PL_WROCLAW,
     "query": "Хочу манікюр з гель-лаком",
     "expect_any": ["манікюр", "гель", "лак"]},
    {"id": 17, "country": "gb", "salon": GB_OXFORD,
     "query": "I want balayage for blonde hair",
     "expect_any": ["balayage", "балаяж"]},
    {"id": 18, "country": "gb", "salon": GB_BROMPTON,
     "query": "Хочу японський манікюр для зміцнення нігтів",
     "expect_any": ["япон", "манікюр", "p-shine"]},
    {"id": 19, "country": "gb", "salon": GB_OXFORD,
     "query": "Хочу масаж спини глибокий, після тренувань",
     "expect_any": ["масаж", "спин", "глибок", "спорт"]},
    {"id": 20, "country": "ua", "salon": UA_KYIV_OBOLON,
     "query": "Чи є у вас видалення татуювань лазером?",
     "expect_any": ["немає", "не пропоную", "не пропонує", "на жаль", "не маємо", "не робимо", "відсутн"]},
]


async def onboard(c: httpx.AsyncClient, country: str, salon: str) -> None:
    r = await c.post(f"{BASE}/api/chat/country", data={"country": country})
    r.raise_for_status()
    r = await c.post(f"{BASE}/api/chat/salon", data={"salon_id": salon})
    r.raise_for_status()


async def send(c: httpx.AsyncClient, text: str) -> str:
    r = await c.post(f"{BASE}/api/chat/send", data={"text": text}, timeout=180.0)
    r.raise_for_status()
    return r.json().get("reply", "")


async def run(scn: dict) -> dict:
    print(f"\n[{scn['id']:>2}] {scn['country'].upper()} | {scn['query'][:65]}")
    try:
        async with httpx.AsyncClient(timeout=180.0) as c:
            try:
                await c.get(f"{BASE}/chat/restart")
            except Exception:
                pass
            await onboard(c, scn["country"], scn["salon"])
            reply = await send(c, scn["query"])
    except Exception as exc:
        return {"id": scn["id"], "passed": False, "error": str(exc)}

    rl = reply.lower()
    hit = any(e.lower() in rl for e in scn["expect_any"])
    print(f"     A: {reply[:200].replace(chr(10),' ')}{'...' if len(reply) > 200 else ''}")
    return {"id": scn["id"], "passed": hit, "reply": reply,
            "expected": scn["expect_any"], "hit": hit}


async def main() -> None:
    results = []
    for scn in SCENARIOS:
        res = await run(scn)
        results.append(res)
        await asyncio.sleep(2)

    print("\n" + "=" * 70)
    passed = sum(1 for r in results if r.get("passed"))
    for r in results:
        st = "PASS" if r.get("passed") else "FAIL"
        print(f"  {st} [{r['id']:>2}]")
    print(f"\nTotal: {passed}/{len(results)} passed")
    if passed < len(results):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
