"""Тест vector search: типові запити клієнтів різними мовами.

Для кожного запиту перевіряємо, що серед top-5 результатів є очікувана послуга.
Запуск: python -m scripts.test_search_vectors
"""
from __future__ import annotations

import asyncio
import sys

from app.adapters.embeddings.openai_embedder import OpenAIEmbedder
from app.infrastructure.db.repositories.service_repo import ServiceRepository
from app.infrastructure.db.session import build_engine, build_session_factory, country_session

# (country, salon_id, query, expected_substring_in_result)
# expected — підрядок який має бути в name однієї з top-5 послуг (case-insensitive)
TEST_CASES: list[tuple[str, str | None, str, list[str]]] = [
    # ── UA: Українські запити ──
    ("ua", None, "хочу пофарбувати волосся", ["фарбування", "окрашивание"]),
    ("ua", None, "стрижка жіноча", ["стрижка"]),
    ("ua", None, "зробити манікюр", ["манікюр", "маникюр", "manicure"]),
    ("ua", None, "педикюр", ["педикюр", "pedicure"]),
    ("ua", None, "хочу брови підкоригувати", ["брів", "бров"]),
    ("ua", None, "ламінування вій", ["вій", "ресниц", "lash"]),
    ("ua", None, "мелірування балаяж", ["мелірування", "балаяж", "balayage"]),
    ("ua", None, "ботокс обличчя", ["ботокс", "botox"]),
    ("ua", None, "масаж антицелюлітний", ["масаж", "массаж", "massage", "целюліт", "cellulite"]),
    ("ua", None, "чистка обличчя", ["чистка", "чистк", "cleansing"]),
    ("ua", None, "гель лак", ["гель-лак", "gel"]),
    ("ua", None, "кератинове лікування волосся", ["кератин", "keratin", "лікування", "treatment"]),
    ("ua", None, "зняти гель лак", ["зняття", "знят", "removal"]),
    ("ua", None, "укладка", ["укладка", "styling"]),
    ("ua", None, "SPA кератин для волосся", ["кератин", "keratin"]),

    # ── RU: Русскоязычные запросы ──
    ("ua", None, "покрасить волосы", ["фарбування", "окрашивание", "color"]),
    ("ua", None, "женская стрижка", ["стрижка", "haircut"]),
    ("ua", None, "сделать маникюр", ["манікюр", "маникюр", "manicure"]),
    ("ua", None, "коррекция бровей", ["брів", "бров", "brow"]),
    ("ua", None, "ламинирование ресниц", ["вій", "ресниц", "lash"]),
    ("ua", None, "мелирование", ["мелірування", "мелирование", "highlights"]),
    ("ua", None, "ботокс лоб", ["ботокс", "botox", "лоб", "forehead"]),
    ("ua", None, "антицеллюлитный массаж", ["масаж", "массаж", "massage", "целюліт"]),
    ("ua", None, "чистка лица", ["чистка", "cleansing", "обличчя", "лица"]),
    ("ua", None, "гель лак", ["гель-лак", "gel"]),
    ("ua", None, "кератин для волос", ["кератин", "keratin"]),
    ("ua", None, "снять гель лак", ["зняття", "снятие", "removal"]),

    # ── EN: English queries ──
    ("ua", None, "hair coloring", ["фарбування", "color", "окрашивание"]),
    ("ua", None, "women haircut", ["стрижка", "haircut"]),
    ("ua", None, "manicure", ["манікюр", "маникюр", "manicure"]),
    ("ua", None, "eyebrow correction", ["брів", "бров", "brow", "eyebrow"]),
    ("ua", None, "eyelash lamination", ["вій", "ресниц", "lash", "ламінування"]),
    ("ua", None, "balayage highlights", ["мелірування", "балаяж", "balayage", "highlight"]),
    ("ua", None, "facial botox", ["ботокс", "botox"]),
    ("ua", None, "anti cellulite massage", ["масаж", "массаж", "massage", "целюліт"]),
    ("ua", None, "facial cleansing", ["чистка", "cleansing"]),
    ("ua", None, "gel polish", ["гель-лак", "gel"]),
    ("ua", None, "keratin treatment", ["кератин", "keratin"]),
    ("ua", None, "gel polish removal", ["зняття", "снятие", "removal"]),

    # ── PL: Polskie zapytania ──
    ("ua", None, "farbowanie włosów", ["фарбування", "color", "farbowanie"]),
    ("ua", None, "strzyżenie damskie", ["стрижка", "haircut", "strzyżenie"]),
    ("ua", None, "manicure", ["манікюр", "маникюр", "manicure"]),
    ("ua", None, "korekta brwi", ["брів", "бров", "brow", "brwi"]),
    ("ua", None, "laminowanie rzęs", ["вій", "ресниц", "lash", "rzęs"]),
    ("ua", None, "masaż antycellulitowy", ["масаж", "массаж", "massage"]),
    ("ua", None, "oczyszczanie twarzy", ["чистка", "cleansing", "oczyszczanie"]),
    ("ua", None, "żel-lakier", ["гель-лак", "gel", "żel"]),
]

TOP_K = 5


async def run_tests() -> None:
    engine = build_engine()
    factory = build_session_factory(engine)
    embedder = OpenAIEmbedder()

    # Знаходимо салон з найбільшою кількістю послуг
    from sqlalchemy import text as sql_text
    async with country_session(factory, "ua") as session:
        row = await session.execute(sql_text(
            "SELECT salon_id, COUNT(*) c FROM service WHERE archive = false "
            "GROUP BY salon_id ORDER BY c DESC LIMIT 1"
        ))
        salon_id = row.first()[0]

    print(f"Testing salon: {salon_id}")
    print(f"{'=' * 90}")

    passed = 0
    failed = 0
    results_log: list[str] = []

    for country, sid_override, query, expected_subs in TEST_CASES:
        test_salon = sid_override or salon_id
        async with country_session(factory, country) as session:
            repo = ServiceRepository(session, embedder=embedder)
            services = await repo.search(
                salon_id=test_salon,
                query=query,
                limit=TOP_K,
            )

        # Перевіряємо, чи хоча б один результат містить один з expected
        names = [svc.name.lower() for svc in services]
        names_uk = [(svc.name_uk or "").lower() for svc in services]
        names_ru = [(svc.name_ru or "").lower() for svc in services]
        names_en = [(svc.name_en or "").lower() for svc in services]
        names_pl = [(svc.name_pl or "").lower() for svc in services]
        all_names = []
        for i in range(len(services)):
            combined = f"{names[i]} {names_uk[i]} {names_ru[i]} {names_en[i]} {names_pl[i]}"
            all_names.append(combined)

        hit = False
        for combined in all_names:
            for exp in expected_subs:
                if exp.lower() in combined:
                    hit = True
                    break
            if hit:
                break

        status = "PASS" if hit else "FAIL"
        if hit:
            passed += 1
        else:
            failed += 1

        top1 = services[0].name if services else "(no results)"
        line = f"[{status}] \"{query}\" → top1: {top1}"
        print(line)
        if not hit:
            print(f"       Expected one of: {expected_subs}")
            print(f"       Got: {[s.name for s in services[:3]]}")
        results_log.append(line)

    print(f"\n{'=' * 90}")
    print(f"Results: {passed}/{passed + failed} passed ({passed * 100 // (passed + failed)}%)")
    if failed:
        print(f"FAILED: {failed} tests")

    await engine.dispose()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(run_tests())
