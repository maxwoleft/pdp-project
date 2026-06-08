"""Phase 2: масово заповнити expert content для всіх profiles (UA/PL/GB).

Класифікатор `classify_profile()` маппить profile name → template_key.
Скрипт UPSERT-ить translation (default lang uk) з полями з templates.py.

Запуск:
    python -m scripts.fill_profile_content --country ua --apply
    python -m scripts.fill_profile_content --country pl --apply
    python -m scripts.fill_profile_content --country gb --apply
"""
from __future__ import annotations

import argparse
import asyncio
import re
from collections import Counter

from sqlalchemy import text

from app.infrastructure.db.session import build_engine, build_session_factory
from scripts.expert_templates.templates import DOMAIN_TEMPLATES


# Класифікатор: substring patterns у profile name (lowercased) → template_key.
# Specific patterns мають іти ПЕРЕД generic.
CLASSIFY_RULES: list[tuple[str, str]] = [
    # ── Чоловічі послуги (consolidated bucket) ────────────────────────────
    ("борода|barber|боро та вуса", "men_beard"),
    ("чоловіче фарбування|камуфляж", "men_camouflage"),
    ("чоловіч.*манікюр", "manikyur_men"),
    ("чоловіч.*педикюр", "pedikyur_men"),
    ("чоловіч.*стрижк", "haircut_male"),
    ("чоловіч.*укладк", "hair_styling_basic"),
    ("корекц.*брів.*чоловіч", "brows_men"),

    # ── Нігті (порядок: nail → manikyur → pedikyur) ───────────────────────
    ("нарощуван.*нігт|нарощу.*ніг", "nail_extension"),
    ("корекц.*нарощен.*нігт|корекц.*нарощуван", "nail_correction"),
    ("дизайн.*нігт|дизайн", "nail_design"),
    ("ремонт.*нігт", "nail_repair"),
    ("укріплен.*ibx|укріплен.*ніг", "nail_strengthening"),
    ("укріплен.*гел", "nail_strengthening"),
    ("полірування.*нігт", "nail_strengthening"),
    ("форма.*нігт", "nail_strengthening"),
    ("зняття.*гель|зняття.*нарощ.*нігт|зняття.*покрит", "nail_removal"),
    ("ламінуван.*нігт", "nail_strengthening"),

    # ── Манікюр / Педикюр ─────────────────────────────────────────────────
    ("спа.*манікюр|spa.*манік|spa-манікюр", "spa_hands"),
    ("спа.*педикюр|spa.*педик|spa-педикюр|spa.*стоп", "spa_feet"),
    ("парафін.*для рук|парафін.*рук", "spa_hands"),
    ("парафін.*для ніг|парафін.*ніг", "spa_feet"),
    ("спа.*догляд.*рук|spa.*догляд.*рук", "spa_hands"),
    ("спа.*догляд.*стоп|spa.*догляд.*стоп|spa.*догляд.*ніг", "spa_feet"),
    ("манікюр.*покритт.*гель|манікюр.*гель.*лак", "manikyur_gel"),
    ("педикюр.*покритт.*гель|педикюр.*гель.*лак", "pedikyur_gel"),
    ("педикюр.*япон|япон.*педикюр", "pedikyur_yaponskyi"),
    ("манікюр.*япон|япон.*манікюр", "manikyur_yaponskyi"),
    ("педикюр.*класич|класич.*педикюр", "pedikyur_classic"),
    ("манікюр.*класич|класич.*манікюр", "manikyur_classic"),
    ("частковий.*педикюр|подологічний.*педикюр", "podology_general"),
    ("педикюр", "pedikyur_classic"),
    ("манікюр", "manikyur_classic"),

    # ── Подологія ─────────────────────────────────────────────────────────
    ("вросший.*ніготь|онихокрипт|врослий", "podology_ingrown"),
    ("гіперкератоз|натопти|мозол", "podology_calluses"),
    ("тріщин.*стоп|обробка.*оніхо", "podology_general"),
    ("ортез|протезу.*нігт", "podology_orthos"),
    ("подолог", "podology_general"),
    ("тейпуван.*стоп", "taping_foot"),
    ("лікувальн.*покрит", "podology_general"),

    # ── Фарбування / висвітлення / тонування ──────────────────────────────
    ("airtouch|ейртач", "hair_highlight_airtouch"),
    ("balayage|балаяж", "hair_highlight_balayage"),
    ("shatush|шатуш", "hair_highlight_shatush"),
    ("highlights", "hair_highlight_generic"),
    ("face\\s*framing", "hair_highlight_face_framing"),
    ("мелірув|меліров", "hair_highlight_generic"),
    ("освітленн|осветлен|babylights", "hair_highlight_generic"),
    ("блонд.*мит|блонд миття", "hair_color_remove"),
    ("зняття.*кольор|зняття.*кольору", "hair_color_remove"),
    ("техніки.*висвіт", "hair_highlight_generic"),
    ("фарбування.*коренів|фарбування.*корін|фарбування.*коренi", "hair_color_roots"),
    ("фарбування.*перманент", "hair_color_full"),
    ("фарбування.*тонуван", "hair_color_tone"),
    ("тонуван", "hair_color_tone"),
    ("фарбуван", "hair_color_full"),

    # ── Лікування волосся ─────────────────────────────────────────────────
    ("ботокс.*волос|botox.*hair", "hair_treatment_botox"),
    ("керати|brazilian", "hair_treatment_keratin"),
    ("глибокий.*курс.*відновл|глибоке.*відновл", "hair_treatment_general"),
    ("експрес.*догляд.*ампул|експрес.*ампул", "hair_treatment_general"),
    ("лікуван.*волос|відновлен.*волос|догляд.*волос", "hair_treatment_general"),
    ("догляд.*академі|academie", "hair_treatment_general"),
    ("догляд.*casmara", "hair_treatment_general"),
    ("догляд.*dmk", "hair_treatment_general"),
    ("dmk\\b", "hair_treatment_general"),
    ("догляд.*перед.*макіяж", "hair_treatment_general"),
    ("догляд.*преміум", "hair_treatment_general"),
    ("інші.*процедури.*для.*волос", "hair_treatment_general"),

    # ── Стрижки ───────────────────────────────────────────────────────────
    ("дитяча.*стрижк|стрижк.*дит", "haircut_kids"),
    ("стрижк.*чубчик|стрижк.*грив|корекц.*чубчик", "haircut_bang"),
    ("жіноч.*стрижк|стрижк.*жіноч|жіночі.*стрижк", "haircut_female"),
    ("чоловіч.*стрижк|стрижк.*чоловіч|чоловіч стрижк", "haircut_male"),
    ("стрижк", "haircut_female"),

    # ── Укладки ───────────────────────────────────────────────────────────
    ("укладка.*lux|укладка.*люкс|lux.*укладк", "hair_styling_lux"),
    ("укладка.*нарощен|укладк.*нарощ", "hair_styling_extensions"),
    ("афрокудр|афро.*кудр|афронакрут", "hair_styling_afro"),
    ("зачіск.*вечір|вечірн.*зачіск", "hair_evening_styling"),
    ("плетінн|плетіння", "hair_braiding"),
    ("укладк|стайлін|зачіск", "hair_styling_basic"),
    ("жіночі.*укладк|укладк.*жін", "hair_styling_basic"),
    ("голлівудськ.*хвил", "hair_styling_basic"),
    ("блонд миття", "hair_color_remove"),

    # ── Нарощування волосся ───────────────────────────────────────────────
    ("нарощуван.*волос|нарощуван.*волосся", "hair_extension"),
    ("консультац.*волос", "consultation"),

    # ── Брови ─────────────────────────────────────────────────────────────
    ("корекц.*\\+.*фарбуван.*брів|корекц.*брів.*\\+.*фарбуван", "brows_combo"),
    ("ботокс.*брів|біофіксац.*брів", "brows_tint"),
    ("ламінуван.*брів", "brows_lamination"),
    ("фарбуван.*брів", "brows_tint"),
    ("корекц.*брів|форма.*брів|моделюван.*брів", "brows_correction"),
    ("щастя.*брів|happiness.*brow", "brows_lamination"),
    ("депіляц.*обличч|висвітлен.*брів", "brows_correction"),
    ("брови", "brows_correction"),

    # ── Вії ───────────────────────────────────────────────────────────────
    ("нарощуван.*вій|нарощуван.*ві", "lashes_extension"),
    ("ламінуван.*вій", "lashes_lamination"),
    ("фарбуван.*вій", "lashes_tint"),
    ("зняття.*нарощен.*вій", "lashes_extension"),
    ("вії|lashes", "lashes_lamination"),
    ("передпігмент", "makeup_permanent"),
    ("інші.*процедури.*з.*брів|інші.*процедури.*з брів", "brows_correction"),

    # ── Макіяж ────────────────────────────────────────────────────────────
    ("макіяж.*денн|денний.*макіяж", "makeup_day"),
    ("макіяж.*вечір|вечірн.*макіяж", "makeup_evening"),
    ("макіяж.*особлив|макіяж.*події|весільн.*макіяж", "makeup_special"),
    ("макіяж.*коктейл|коктейльн.*макіяж", "makeup_evening"),
    ("перманентн.*макіяж|татуаж|permanent", "makeup_permanent"),
    ("урок.*макіяж", "makeup_lesson"),
    ("спа.*візаж", "spa_hands"),
    ("макіяж.*звичай|макіяж", "makeup_day"),

    # ── Масаж ─────────────────────────────────────────────────────────────
    ("антицелюліт.*масаж|анти-целюліт.*масаж", "massage_anticellulite"),
    ("лімфодренаж", "massage_lymphatic"),
    ("спортивн.*масаж", "massage_sport"),
    ("релакс.*масаж|розслабл.*масаж|relax", "massage_relax"),
    ("масаж.*для вагітн|вагітн.*масаж", "massage_pregnancy"),
    ("масаж.*облич|облич.*масаж", "massage_face"),
    ("локальн.*масаж|масаж.*голов|масаж.*шиї|масаж.*спин", "massage_local"),
    ("класич.*масаж|загальн.*масаж", "massage_classic"),
    ("інші.*види.*масаж|інші.*масаж", "massage_classic"),
    ("масаж", "massage_classic"),

    # ── Депіляція ─────────────────────────────────────────────────────────
    ("воскова.*депіляц|воск.*депіл", "depilation_wax"),
    ("шугарин|цукров.*депіляц", "depilation_sugar"),
    ("лазерн.*епіляц|лазерн.*депіл", "depilation_laser"),
    ("депіляц.*облич|депіл.*верхньої.*губ", "depilation_face"),
    ("інші.*процедури.*депіляц", "depilation_wax"),

    # ── Косметологія ──────────────────────────────────────────────────────
    ("чистка.*обличч|глибока.*чистка", "cosmetology_cleansing"),
    ("чистка.*спин", "cosmetology_cleansing"),
    ("пілінг", "cosmetology_peeling"),
    ("ботулінотерап|ботокс|botox", "cosmetology_botox"),
    ("філлер|контурна.*пластик", "cosmetology_filler"),
    ("мезотерап", "cosmetology_meso"),
    ("біоревіталіз|біоревітал", "cosmetology_biorev"),
    ("карбоксітерап|carbo|карбок", "cosmetology_carboxy"),
    ("rejuran", "cosmetology_rejuran"),
    ("електропорац|мікрострум", "cosmetology_electroporation"),
    ("endospher|ендосфер", "cosmetology_apparat_endosphere"),
    ("kuma\\s*shape|куma\\s*shape", "cosmetology_apparat_kuma"),
    ("lpg", "cosmetology_apparat_lpg"),
    ("icoone|айкун|айкун laser|stratosphere|stratosфера", "cosmetology_apparat_icoone"),
    ("robolex", "cosmetology_apparat_other"),
    ("dermapen|дермапен", "cosmetology_apparat_other"),
    ("термоліфтинг|aft|nir|инфрачерв", "cosmetology_apparat_other"),
    ("dye\\s*vl|фотоомоло", "cosmetology_apparat_other"),
    ("альгінатн.*маск", "cosmetology_mask_alginate"),
    ("видален.*міліум|видален.*папілом|видален.*бородав", "cosmetology_milium_papilloma"),
    ("полінукле|поліклеотид", "cosmetology_meso"),
    ("інші.*процедури.*космет|інші.*косметолог", "cosmetology_premium_care"),
    ("інші.*апаратн", "cosmetology_apparat_other"),

    # ── Обгортання ────────────────────────────────────────────────────────
    ("обгортанн|wrap", "wrap_body"),
    ("інші.*процедури.*обгортанн", "wrap_body"),

    # ── Консультації ──────────────────────────────────────────────────────
    ("консультац", "consultation"),
    ("загальна.*консультац", "consultation"),

    # ── Brand-specific care lines ─────────────────────────────────────────
    ("aquapure|aqua\\s*pure", "cosmetology_apparat_other"),
    ("casmara", "cosmetology_premium_care"),
    ("forll[eé]'?d|forlle'd|forll", "cosmetology_premium_care"),
    ("medik8", "cosmetology_premium_care"),
    ("biologique\\s*recherche", "cosmetology_premium_care"),
    ("estederm", "cosmetology_premium_care"),
    ("axzia", "cosmetology_premium_care"),

    # ── Specific apparatures ──────────────────────────────────────────────
    ("smas\\s*-?\\s*ліфтинг|smas.*lift|hifu", "cosmetology_apparat_other"),
    ("rf\\s*-?\\s*ліфтинг|радіоліфтинг", "cosmetology_apparat_other"),
    ("монализа|monaliza|лазер.*monali", "cosmetology_apparat_other"),
    ("апаратн.*космет|апаратн.*процедур", "cosmetology_apparat_other"),
    ("пресотерап", "cosmetology_apparat_other"),
    ("колаген.*стим|colagen", "cosmetology_meso"),
    ("ліполітик", "cosmetology_meso"),
    ("екзосоми|exosome", "cosmetology_biorev"),
    ("мікроголков|microneedling", "cosmetology_apparat_other"),
    ("пудров.*напилен", "makeup_permanent"),
    ("дзеркальн.*вирівнюван", "hair_treatment_keratin"),

    # ── Generic catchalls ─────────────────────────────────────────────────
    ("біозавивк", "hair_treatment_general"),
    ("депіляц", "depilation_wax"),
    ("шугарінг|шугаринг|віск", "depilation_wax"),
    ("електроепіляц", "depilation_laser"),
    ("проколюван.*вух|проколюван.*нос|пірсин", "consultation"),
    ("перманент", "makeup_permanent"),
    ("дитяч.*зал", "haircut_kids"),
    ("чоловіч.*послуг|men service", "men_beard"),
    ("лікуван.*чоловіч", "men_beard"),
    ("чистк.*догляд|чистки.*догляди", "cosmetology_cleansing"),
    ("доглядов.*космет|космет.*догляд|космет$|^косметолог", "cosmetology_premium_care"),
    ("нігтьов.*серв", "nail_strengthening"),
    ("догляд$|^догляд", "hair_treatment_general"),
]


def classify_profile(name: str) -> str:
    """Profile name → template_key. Returns 'generic_other' якщо немає match."""
    n = (name or "").lower().strip()
    for pattern, template_key in CLASSIFY_RULES:
        if re.search(pattern, n, flags=re.IGNORECASE):
            return template_key
    return "generic_other"


async def amain() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--country", required=True, choices=["ua", "pl", "gb"])
    p.add_argument("--apply", action="store_true")
    p.add_argument("--language", default="uk", help="Translation language to fill")
    args = p.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            profiles = (await session.execute(text(
                "SELECT id, name FROM public.service_profile WHERE country=:c ORDER BY name"
            ), {"c": args.country})).all()
            print(f"[{args.country}] profiles: {len(profiles)}")

            stats: Counter[str] = Counter()
            plan: list[tuple[str, str, str, dict]] = []  # (pid, name, template_key, template)
            for pid, pname in profiles:
                tk = classify_profile(pname)
                stats[tk] += 1
                plan.append((str(pid), pname, tk, DOMAIN_TEMPLATES[tk]))

            print(f"\n=== Classification distribution ({args.country}) ===")
            for tk, cnt in stats.most_common():
                print(f"  {cnt:3d}  {tk}")
            generic_count = stats.get("generic_other", 0)
            print(f"\n  Unclassified (generic_other): {generic_count}")
            if generic_count:
                print(f"  Unclassified profile names:")
                for pid, pname, tk, _ in plan:
                    if tk == "generic_other":
                        print(f"    - {pname}")

            if not args.apply:
                print("\nDRY RUN. Use --apply.")
                return

            # UPSERT translation per profile
            from datetime import datetime
            updated = 0
            for pid, pname, tk, tmpl in plan:
                # Build short_description з sales_pitch або з name
                short_desc = tmpl.get("sales_pitch") or pname
                # Check existing translation
                existing = (await session.execute(text(
                    "SELECT id FROM public.service_profile_translation "
                    "WHERE profile_id=:pid AND language=:lang"
                ), {"pid": pid, "lang": args.language})).first()
                if existing:
                    await session.execute(text("""
                        UPDATE public.service_profile_translation SET
                          short_description = :sd,
                          addresses_problems = CAST(:ap AS json),
                          target_audience = CAST(:ta AS json),
                          benefits = CAST(:b AS json),
                          keywords = CAST(:kw AS json),
                          sales_pitch = :sp,
                          cross_sell = CAST(:cs AS json),
                          updated_at = NOW()
                        WHERE profile_id = :pid AND language = :lang
                    """), {
                        "pid": pid, "lang": args.language,
                        "sd": short_desc,
                        "ap": _json(tmpl["addresses_problems"]),
                        "ta": _json(tmpl["target_audience"]),
                        "b": _json(tmpl["benefits"]),
                        "kw": _json(tmpl["keywords"]),
                        "sp": tmpl.get("sales_pitch") or None,
                        "cs": _json(tmpl["cross_sell"]),
                    })
                else:
                    import uuid as _uuid
                    await session.execute(text("""
                        INSERT INTO public.service_profile_translation
                        (id, profile_id, language, short_description, detailed_description,
                         addresses_problems, target_audience, benefits, keywords,
                         procedure_steps, contraindications, cross_sell,
                         sales_pitch, ckey_overrides,
                         created_at, updated_at)
                        VALUES (:id, :pid, :lang, :sd, NULL,
                                CAST(:ap AS json), CAST(:ta AS json),
                                CAST(:b AS json), CAST(:kw AS json),
                                '[]'::json, '[]'::json, CAST(:cs AS json),
                                :sp, '{}'::jsonb,
                                NOW(), NOW())
                    """), {
                        "id": str(_uuid.uuid4()),
                        "pid": pid, "lang": args.language,
                        "sd": short_desc,
                        "ap": _json(tmpl["addresses_problems"]),
                        "ta": _json(tmpl["target_audience"]),
                        "b": _json(tmpl["benefits"]),
                        "kw": _json(tmpl["keywords"]),
                        "sp": tmpl.get("sales_pitch") or None,
                        "cs": _json(tmpl["cross_sell"]),
                    })
                updated += 1
            await session.commit()
            print(f"\n[{args.country}] UPSERTED {updated} translations.")
    finally:
        await engine.dispose()


def _json(v) -> str:
    import json as _j
    return _j.dumps(v, ensure_ascii=False)


if __name__ == "__main__":
    asyncio.run(amain())
