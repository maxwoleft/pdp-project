"""Клонує UA spec profiles → PL/GB через overlap canonical_keys.

Логіка:
- UA spec profiles (created_by IN ('hair_v1','nails_v1','vizazh_v1','remaining_v1'))
- Для кожного: збираємо ВСІ canonical_keys (primary + family + brand options)
- Перевіряємо overlap з {country}.service canonical_keys
- Якщо overlap > 0: створюємо новий profile country={country} з тим самим content
  + canonical_keys обмежені overlap subset
- Family option keys = всі overlap keys цього profile
- Brand options keys = subset кожного brand option

Заздалегідь видаляємо існуючі PL/GB profiles (catch-all) — re-run catchall окремо потім.

Запуск:
    python -m scripts.clone_ua_profiles_to_country --country pl --apply
    python -m scripts.clone_ua_profiles_to_country --country gb --apply
"""
from __future__ import annotations

import argparse
import asyncio
import uuid

from sqlalchemy import delete, select, text

from app.infrastructure.db.models.profile import (
    ServiceProfile, ServiceProfileOption, ServiceProfileTranslation,
)
from app.infrastructure.db.session import build_engine, build_session_factory


SPEC_CREATORS = ("hair_v1", "nails_v1", "vizazh_v1", "remaining_v1")


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", required=True, choices=["pl", "gb"])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--delete-existing", action="store_true",
                        help="Видалити всі existing profiles цієї country (catch-all) перед clone")
    args = parser.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            # Real keys цієї country
            r = await session.execute(text(
                f"SELECT DISTINCT canonical_key FROM {args.country}.service "
                "WHERE archive=false AND canonical_key IS NOT NULL"
            ))
            country_keys: set[str] = {row[0] for row in r.all() if row[0]}
            print(f"[{args.country}] real keys: {len(country_keys)}")

            # Optionally delete existing
            if args.delete_existing and args.apply:
                d = await session.execute(text(
                    "DELETE FROM public.service_profile WHERE country = :c"
                ), {"c": args.country})
                print(f"  ✖ deleted {d.rowcount} existing profiles for {args.country}")
                await session.flush()

            # UA spec profiles
            ua_rows = (await session.execute(
                select(ServiceProfile)
                .where(ServiceProfile.country == "ua")
                .where(ServiceProfile.created_by.in_(SPEC_CREATORS))
            )).scalars().unique().all()
            print(f"[ua] spec profiles: {len(ua_rows)}")

            cloned = 0
            skipped = 0
            used_primaries: set[str] = set()
            if args.apply and not args.delete_existing:
                existing = await session.execute(text(
                    "SELECT canonical_key FROM public.service_profile WHERE country=:c"
                ), {"c": args.country})
                used_primaries = {r[0] for r in existing.all()}

            for ua_p in ua_rows:
                # Зібрати overlap keys
                family_opts = [o for o in ua_p.options if o.option_type == "family"]
                brand_opts = [o for o in ua_p.options if o.option_type == "brand"]
                all_family_keys: list[str] = []
                for o in family_opts:
                    all_family_keys.extend(o.canonical_keys or [])
                overlap_family = [k for k in all_family_keys if k in country_keys]

                if not overlap_family:
                    skipped += 1
                    continue

                # Primary canonical_key — обрати перший НЕ зайнятий з overlap
                primary = None
                for cand in overlap_family:
                    if cand not in used_primaries:
                        primary = cand
                        break
                if not primary:
                    # Усі overlap keys зайняті — генеруємо placeholder
                    primary = f"clone_{uuid.uuid4().hex[:12]}"

                if not args.apply:
                    print(f"  + clone {ua_p.name:45s} (keys: {len(overlap_family)})")
                    cloned += 1
                    used_primaries.add(primary)
                    continue
                used_primaries.add(primary)

                new_pid = str(uuid.uuid4())
                session.add(ServiceProfile(
                    id=new_pid,
                    canonical_key=primary,
                    name=ua_p.name,
                    country=args.country,
                    default_language=ua_p.default_language or "uk",
                    enabled=True,
                    created_by=f"clone_from_ua_{ua_p.created_by}",
                    updated_by=f"clone_from_ua_{ua_p.created_by}",
                ))

                # Translations (всі мови, не тільки uk)
                for t in ua_p.translations:
                    session.add(ServiceProfileTranslation(
                        profile_id=new_pid,
                        language=t.language,
                        short_description=t.short_description,
                        detailed_description=t.detailed_description,
                        addresses_problems=list(t.addresses_problems or []),
                        target_audience=list(t.target_audience or []),
                        benefits=list(t.benefits or []),
                        keywords=list(t.keywords or []),
                        procedure_steps=list(t.procedure_steps or []),
                        contraindications=list(t.contraindications or []),
                        aftercare_advice=t.aftercare_advice,
                        cross_sell=list(t.cross_sell or []),
                        duration_typical_min=t.duration_typical_min,
                        sales_pitch=t.sales_pitch,
                    ))

                # Family options (з overlap subset)
                for o in family_opts:
                    o_overlap = [k for k in (o.canonical_keys or []) if k in country_keys]
                    session.add(ServiceProfileOption(
                        profile_id=new_pid,
                        option_type="family",
                        name=o.name,
                        sort_order=o.sort_order,
                        short_description=o.short_description,
                        detailed_description=o.detailed_description,
                        addresses_problems=list(o.addresses_problems or []),
                        target_audience=list(o.target_audience or []),
                        benefits=list(o.benefits or []),
                        keywords=list(o.keywords or []),
                        when_to_recommend=o.when_to_recommend,
                        sales_pitch=o.sales_pitch,
                        cross_sell=list(o.cross_sell or []),
                        procedure_steps=list(o.procedure_steps or []),
                        contraindications=list(o.contraindications or []),
                        aftercare_advice=o.aftercare_advice,
                        canonical_keys=o_overlap,
                    ))

                # Brand options — клонуємо тільки ті у яких є overlap
                for o in brand_opts:
                    o_overlap = [k for k in (o.canonical_keys or []) if k in country_keys]
                    if not o_overlap:
                        continue
                    session.add(ServiceProfileOption(
                        profile_id=new_pid,
                        option_type="brand",
                        name=o.name,
                        sort_order=o.sort_order,
                        short_description=o.short_description,
                        detailed_description=o.detailed_description,
                        addresses_problems=list(o.addresses_problems or []),
                        target_audience=list(o.target_audience or []),
                        benefits=list(o.benefits or []),
                        keywords=list(o.keywords or []),
                        when_to_recommend=o.when_to_recommend,
                        sales_pitch=o.sales_pitch,
                        cross_sell=list(o.cross_sell or []),
                        procedure_steps=list(o.procedure_steps or []),
                        contraindications=list(o.contraindications or []),
                        aftercare_advice=o.aftercare_advice,
                        canonical_keys=o_overlap,
                    ))
                cloned += 1
                print(f"  + cloned [{args.country}] {ua_p.name:45s} keys={len(overlap_family)}")

            if args.apply:
                await session.commit()
                print(f"\nDONE [{args.country}]: cloned={cloned}, skipped_no_overlap={skipped}")
            else:
                print(f"\nDRY RUN [{args.country}]: would clone {cloned}, skip {skipped}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
