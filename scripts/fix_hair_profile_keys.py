"""Fix invalid canonical_keys у hair_v1 profiles + додаємо missing real keys.

Запуск:
    python -m scripts.fix_hair_profile_keys --apply
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select, text

from app.infrastructure.db.models.profile import ServiceProfile, ServiceProfileOption
from app.infrastructure.db.session import build_engine, build_session_factory


# profile.name → set(canonical_keys) — повний replacement з реальними ключами
FIXES: dict[str, list[str]] = {
    "Стрижка жіноча": ["stryzhka_zhinocha"],
    "Дитяча стрижка": [
        "dytyacha_stryzhka",
        "6_do_dytyacha_rokiv_stryzhka",
        "6_dytyacha_modelna_pislya_rokiv_stryzhka",
        "6_dytyacha_mashynkoyu_pislya_rokiv_stryzhka",
        "7_cholovicha_do_dytyacha_rokiv_stryzhka",
    ],
    "Укладка LUX": [
        "lyuks_ukladka", "lyuks_ukladannya",
        "garyachyi_instrument_kosmetytsi_lyuks_na_na_ukladannya",
        "garyachyi_instrument_na_ukladka", "kosmetytsi_lyuks_na_ukladannya",
    ],
    "Голлівудські хвилі": ["gollivudska_khvylya"],
    "Зачіска вечірня": ["zachiska", "vesilna_zachiska"],
    "Тонування": [
        "tonuvannya", "color_gloss_tonuvannya", "tonuvannya_ukladka",
        "tint_tone_tonuvannya_ukladka", "bezamiachne_tonuvannya_ukladka",
        "bezamiachne_tonuvannya_vibrance",
    ],
    "Зняття кольору": [
        "koloru_pudroyu_znyattya", "koloru_kyslotne_znyattya", "koloru_pudra_znyattya",
        "koloru_organichne_znyattya", "chornogo_vykhid_z", "organichna_zmyvka",
        "koloru_neitralizatsiya",
    ],
    "Total Blond": [
        "blond_farbuvannya_total", "blond_total",
        "farbuvannya_spetsblond", "farbuvannya_spetsblond_ukladka",
        "blond_odnotonne_osvitlennya_super",
        "balayage_blond_osvitlennya_super_tekhnika",
        "airtouch_blond_osvitlennya_super_tekhnika",
        "blond_osvitlennya_shatush_super_tekhnika",
        "blond_handtouch_osvitlennya_super_tekhnika",
    ],
    "Догляд преміум-брендами": [
        "brae_hadat_ukladka", "brae_hadat_lyuks_ukladka",
        "brae_hadat_naroshchenogo_ukladka_volossya",
        "brae_hadat_lyuks_naroshchenogo_ukladka_volossya",
        "brae_hadat_stryzhka",
        "detoks_dlya_golovy_khadat_rozumnyi_shkyry_ta_volossya",
        "formula_glybokogo_khadat_sekretna_zvolozhennya",
        "infenom_spa", "hair_innovatis_ukladka",
        "hair_innovatis_regeneration_total_ukladka",
        "doglyad_ekspres_one_ukladka",
        "doglyad_golovy_one_povnyi_shkiroyu_ta_ukladka_volossyam_za",
        "active_hita_plex_tretment",
    ],
    "Кератинове випрямлення / Ботокс для волосся": [
        "blowout_brazilian_keratyn_ukladka", "keratyn_luxliss_ukladka",
        "dzerkalne_vyrivnyuvannya", "keratynove_vyrivnyuvannya",
    ],
    "Нарощування волосся": [
        "100g_naroshchuvannya_volossya", "150g_naroshchuvannya_volossya",
        "50g_naroshchuvannya_volossya", "100_g_naroshchuvannya_volossya",
        "100g_kapsulne_naroshchuvannya_volossya", "150g_kapsulne_naroshchuvannya_volossya",
        "50g_kapsulne_naroshchuvannya_volossya",
        "kapsulamy_mikro_naroshchuvannya_volossya",
        "kapsulamy_nano_naroshchuvannya_volossya",
        "naroshchenym_robota_volossyam_z",
        "1_kapsulne_naroshchuvannya_shtuka_volossya",
        "naroshchennogo_volossya_znyattya", "100g_naroshchenogo_volossya_znyattya",
        "naroshchenogo_volossya_znyattya",
    ],
}


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            # Validate keys exist
            all_real: set[str] = set()
            for c in ("ua", "pl", "gb"):
                rows = await session.execute(text(
                    f"SELECT DISTINCT canonical_key FROM {c}.service "
                    "WHERE archive=false AND canonical_key IS NOT NULL"
                ))
                all_real.update(r[0] for r in rows.all() if r[0])

            updated = 0
            for profile_name, new_keys in FIXES.items():
                valid_keys = [k for k in new_keys if k in all_real]
                missing = [k for k in new_keys if k not in all_real]
                if missing:
                    print(f"  ⚠ {profile_name}: missing {missing}")
                if not valid_keys:
                    print(f"  ✖ {profile_name}: nothing valid to set")
                    continue

                profile = (await session.execute(
                    select(ServiceProfile).where(ServiceProfile.name == profile_name)
                )).scalar_one_or_none()
                if not profile:
                    print(f"  ✖ {profile_name}: profile not found")
                    continue

                family_opt = (await session.execute(
                    select(ServiceProfileOption).where(
                        ServiceProfileOption.profile_id == profile.id,
                        ServiceProfileOption.option_type == "family",
                    )
                )).scalar_one_or_none()
                if not family_opt:
                    print(f"  ✖ {profile_name}: family option not found")
                    continue

                print(f"  ✓ {profile_name}: {len(valid_keys)} keys (was {len(family_opt.canonical_keys or [])})")
                if args.apply:
                    family_opt.canonical_keys = valid_keys
                    # Якщо primary profile.canonical_key — orphan, замінюємо на перший valid
                    if profile.canonical_key not in all_real:
                        # Двофазний swap щоб уникнути UNIQUE collision
                        tmp = f"__tmp__{profile.id}"
                        await session.execute(text(
                            "UPDATE public.service_profile SET canonical_key=:t WHERE id=:i"
                        ), {"t": tmp, "i": profile.id})
                        await session.execute(text(
                            "UPDATE public.service_profile SET canonical_key=:k WHERE id=:i"
                        ), {"k": valid_keys[0], "i": profile.id})
                    updated += 1

            if args.apply:
                await session.commit()
                print(f"\nDONE: updated {updated} profiles")
            else:
                print("\nDRY RUN")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
