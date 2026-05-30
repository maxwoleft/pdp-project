"""Розширює canonical_keys[] у family-options існуючих Hair profiles
шляхом семантичного маппінгу uncovered keys.

Запуск:
    python -m scripts.extend_hair_profile_keys           # dry
    python -m scripts.extend_hair_profile_keys --apply
"""
from __future__ import annotations

import argparse
import asyncio
import re

from sqlalchemy import select, text, update

from app.infrastructure.db.models.profile import (
    ServiceProfile, ServiceProfileOption,
)
from app.infrastructure.db.session import build_engine, build_session_factory


# Regex patterns → target Hair profile name.
# Order matters — perша співпадіння виграє.
MAPPING: list[tuple[str, str]] = [
    # МЕЛІРУВАННЯ / ОСВІТЛЕННЯ техніки (specific перед загальним)
    (r"osvitlennya_tekhnika|tekhnika_tonuvannya|meliruvannya|balayazh|airtouch|shatush|vual|kontur|babylights|lights|venetsian|kaliforn|diagonal|napiv_osvitlennya|krem_osvitlyuyuchyi|osvitlennya_tonuvannya|alfaparf_osvitlennya|efekt_sonyachnyi_tonuvannya", "Мелірування"),
    # ВИХІД З ЧОРНОГО / ЗНЯТТЯ КОЛЬОРУ
    (r"znyattya|zmyvka|vykhid_z_chornogo|chornogo_vykhid|chornogo_farbuvannya_iz|tempting", "Зняття кольору"),
    # ПЕРЕДПІГМЕНТАЦІЯ
    (r"peredpigment", "Передпігментація"),
    # AFRO / ПЛЕТІННЯ
    (r"afronakrutka", "Афрокудрі"),
    (r"afrokudri", "Афрокудрі"),
    (r"pletinnya|kosychok|dekor_pir|pir_v_volossya|piryachko", "Плетіння"),
    # ВЕСІЛЬНА / ГОЛЛІВУДСЬКА / ЗАЧІСКА
    (r"vesilna|svyatkova|urochysta", "Зачіска вечірня"),
    (r"gollivud|khvylya|khvyli", "Голлівудські хвилі"),
    (r"zachiska", "Зачіска вечірня"),
    # ЛІКУВАННЯ / БРЕНД-ДОГЛЯДИ
    (r"detoks_golovy_shkiry", "Експрес-догляд ампулами"),
    (r"awg_likuvannya|ekspres_likuvannya|ampul", "Експрес-догляд ампулами"),
    (r"shchaslyve_ukladka|shchastya|absolyutne_dlya|absolyutne_likuvannya|likuvannya_volossya_lebel", "Глибокий курс відновлення"),
    (r"milbon|cronna|tokio_inkarami|inkarami|protsedura_vidnovlennya|protsedura_milbon|spa_doglyad|doglyad_milbon|doglyad_brae|brae_revival|rekonstruktsiya_revival|brae_power|brae_bond|bond_angel|brae_povnyi_rytual|brae_ekspres_rytual|farbuvanni_pry_zakhyst|zakhyst_brae|masla_osvitlyuyuchi|volossya_zakhyst|pcc_zakhyst|povnyi_rytual|povnyi_revival_rytual|^one$|lebel_one|dnya_natkhnennya|oribe_rytual", "Догляд преміум-брендами"),
    (r"likuvannya|doglyad|rekonstrukts|spa_protsedura|pid_vologa_zamkom|vologa_zamkom|dlya_maska_volossya|maslom_obgortannya|piling_shkiry|piling_golovy|krem_skrab_spa|golovy_ochyshchennya|ochyshchennya_shkiry|volossya_zmitsnennya|patchi_vid", "Глибокий курс відновлення"),
    # БІОЗАВИВКА
    (r"biozavyvka|zavyvka|himichna_zavyvka", "Укладка"),
    # ТОНУВАННЯ (без техніки)
    (r"tonuvannya_vibrance|tonuvannya_color_gloss|color_gloss_tonuvannya|color_gloss|farbuvannya_tint_tone|sushka_tonuvannya|goldwell_tonuvannya|tonuvannya_yellow|tonuvannya_goldwell|alfaparf_tonuvannya", "Тонування"),
    (r"^tonuvannya_ukladka$|^tonuvannya$|^farbuvannya_korinnya_tonuvannya$|^tonuvannya_dovzhyna$", "Тонування"),
    # ФАРБУВАННЯ КОРЕНІВ
    (r"korinnya|koreniv|farbuvannya_korinnya|farbuvannya_korenya|spetsblond|odnotonne|farbuvannya_kolorove|farbuvannya_keune|kolorove|color_farbuvannya_gloss|farbuvannya_ukladka|farbuvannya_pry|farbuvannya_keratin", "Фарбування коренів"),
    # ФАРБУВАННЯ загальне
    (r"farbuvannya|kolor", "Фарбування"),
    # БЛОНД
    (r"blond_total|total_blond|blond_farbuvannya_total|blond_odnotonne|super_blond", "Total Blond"),
    (r"blond_myttya|blond", "Блонд миття"),
    # УКЛАДКА LUX
    (r"lyuks_ukladka|kosmetytsi_lyuks_na_ukladka|kosmetytsi_lyuks_na_ukladannya|garyachyi_instrument_kosmetytsi_lyuks|oribe_ukladka|lyuks_oribe|balmain|kosmetytsi_lyuks_myttya", "Укладка LUX"),
    # УКЛАДКА НАРОЩЕНОГО
    (r"naroshche", "Укладка нарощеного волосся"),
    # УКЛАДКА базова
    (r"lokony_ukladka|garyachyi_instrument_na_ukladka|ukladannya|ukladka|^hair_long$|long_hair", "Укладка"),
    # СТРИЖКА
    (r"chubchyk", "Стрижка чубчика"),
    (r"dytyacha|dytyna|dityachoi", "Дитяча стрижка"),
    (r"stryzhka_zhinocha|stryzhka|poliruvannya_volossya|poliruvannya", "Стрижка жіноча"),
    # КЕРАТИН / БОТОКС
    (r"keratyn|keratin|botoks|botox|blowout_brazilian|brazilian_blowout|vypryamlennya", "Кератинове випрямлення / Ботокс для волосся"),
    # НАРОЩУВАННЯ
    (r"naroshchuvannya|naroshchuvann", "Нарощування волосся"),
]


def classify(key: str, name_sample: str) -> str | None:
    text = f"{key} | {name_sample}".lower()
    for pattern, target in MAPPING:
        if re.search(pattern, text):
            return target
    return None


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            covered_rows = await session.execute(text("""
                SELECT canonical_key FROM service_profile WHERE canonical_key IS NOT NULL
                UNION
                SELECT jsonb_array_elements_text(canonical_keys)
                FROM service_profile_option WHERE canonical_keys IS NOT NULL
            """))
            covered = {r[0] for r in covered_rows.all() if r[0]}

            hair_rows = await session.execute(text("""
                SELECT s.canonical_key, MIN(s.name) AS sample, COUNT(*) AS cnt
                FROM (
                  SELECT canonical_key, name FROM ua.service WHERE archive=false AND canonical_key IS NOT NULL
                  UNION ALL
                  SELECT canonical_key, name FROM pl.service WHERE archive=false AND canonical_key IS NOT NULL
                  UNION ALL
                  SELECT canonical_key, name FROM gb.service WHERE archive=false AND canonical_key IS NOT NULL
                ) s
                JOIN (
                  SELECT DISTINCT c.canonical_key
                  FROM (
                    SELECT canonical_key, category_id FROM ua.service WHERE archive=false
                    UNION ALL
                    SELECT canonical_key, category_id FROM pl.service WHERE archive=false
                    UNION ALL
                    SELECT canonical_key, category_id FROM gb.service WHERE archive=false
                  ) c
                  WHERE c.canonical_key IS NOT NULL
                ) hk ON s.canonical_key = hk.canonical_key
                GROUP BY s.canonical_key
            """))
            all_keys = [(r[0], r[1], r[2]) for r in hair_rows.all()]

            hair_uncovered = []
            for k, name, cnt in all_keys:
                if k in covered:
                    continue
                low = f"{k} | {name}".lower()
                hair_markers = ["volos", "ukladka", "farbuvannya", "kolor", "meliru", "stryzhka",
                                "blond", "tonuvannya", "afro", "pletin", "kosychok", "zachiska",
                                "keratyn", "biozavyvka", "naroshche", "lebel", "brae", "balmain",
                                "milbon", "cronna", "tokio", "inkarami", "biosthetique", "vibrance",
                                "schwarzkopf", "schwarskopf", "oribe", "perukar", "znyattya", "zmyvka",
                                "peredpigment", "vykhid_z_chornogo", "chornogo_vykhid", "khvyl",
                                "gollivud", "vesilna", "ampul", "doglyad", "detoks", "rekonstrukts",
                                "spa_procedura", "spa_protsedura", "spa_doglyad", "shchastya",
                                "shchaslyve", "absolyutne", "rytual", "myttya", "stryzhk", "chubchyk"]
                if not any(m in low for m in hair_markers):
                    continue
                hair_uncovered.append((k, name, cnt))

            print(f"Hair uncovered: {len(hair_uncovered)} keys")

            profiles = await session.execute(
                select(ServiceProfile.id, ServiceProfile.name).where(ServiceProfile.created_by == "hair_v1")
            )
            name_to_id = {n: pid for pid, n in profiles.all()}
            print(f"Hair profiles available: {len(name_to_id)}")

            from collections import defaultdict
            buckets: dict[str, list[str]] = defaultdict(list)
            unclassified: list[tuple[str, str, int]] = []

            for k, name, cnt in hair_uncovered:
                target = classify(k, name or "")
                if target and target in name_to_id:
                    buckets[target].append(k)
                else:
                    unclassified.append((k, name, cnt))

            print(f"\nClassified buckets:")
            total_classified = 0
            for tgt, keys in sorted(buckets.items(), key=lambda x: -len(x[1])):
                print(f"  {tgt:50s} +{len(keys)} keys")
                total_classified += len(keys)
            print(f"\nTotal classified: {total_classified}")
            print(f"Unclassified: {len(unclassified)}")
            if unclassified[:15]:
                print("\nTop 15 unclassified:")
                for k, n, c in sorted(unclassified, key=lambda x: -x[2])[:15]:
                    print(f"  {c:3d} {k:55s} | {(n or '')[:60]}")

            if not args.apply:
                print("\nDRY RUN")
                return

            updated_options = 0
            for tgt, new_keys in buckets.items():
                pid = name_to_id[tgt]
                opt_row = await session.execute(
                    select(ServiceProfileOption)
                    .where(ServiceProfileOption.profile_id == pid)
                    .where(ServiceProfileOption.option_type == "family")
                )
                opt = opt_row.scalar_one_or_none()
                if not opt:
                    print(f"  ⚠ {tgt}: no family option")
                    continue
                current = list(opt.canonical_keys or [])
                merged = list(dict.fromkeys(current + new_keys))
                opt.canonical_keys = merged
                opt.embedding = None
                updated_options += 1
                print(f"  → {tgt:50s} {len(current)} → {len(merged)} keys")

            await session.commit()
            print(f"\nDONE: updated {updated_options} family options. Re-run embed_options.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
