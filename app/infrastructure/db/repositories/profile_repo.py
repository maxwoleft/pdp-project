"""Repository для service profiles, translations, variants, versions, overrides.

Hybrid (B + C):
- Structured CRUD через SQLAlchemy
- Vector search через pgvector cosine_distance
- Sync embedding на save (без черги)
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.infrastructure.db.models.catalog import Service
from app.infrastructure.db.models.profile import (
    ServiceProfile,
    ServiceProfileOverride,
    ServiceProfileTranslation,
    ServiceProfileVariant,
    ServiceProfileVariantEvent,
    ServiceProfileVersion,
)

log = logging.getLogger(__name__)


class ServiceProfileRepository:
    def __init__(self, session: AsyncSession, embedder=None) -> None:
        self.session = session
        self._embedder = embedder

    # ── CRUD: Profile ─────────────────────────────────────────────

    async def list_all(
        self,
        country: str | None = None,
        salon_id: str | None = None,
        search: str | None = None,
        enabled_only: bool = False,
    ) -> list[ServiceProfile]:
        stmt = select(ServiceProfile).order_by(ServiceProfile.name)
        if enabled_only:
            stmt = stmt.where(ServiceProfile.enabled.is_(True))
        if country:
            stmt = stmt.where(ServiceProfile.country == country)
        if search:
            term = f"%{search}%"
            stmt = stmt.where(ServiceProfile.name.ilike(term))

        rows = list((await self.session.execute(stmt)).scalars().unique().all())
        if salon_id:
            rows = [r for r in rows if salon_id in (r.salon_ids or [])]
        return rows

    async def get(self, profile_id: str) -> ServiceProfile | None:
        stmt = select(ServiceProfile).where(ServiceProfile.id == profile_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_canonical_key(
        self, key: str, country: str | None = None
    ) -> ServiceProfile | None:
        stmt = select(ServiceProfile).where(ServiceProfile.canonical_key == key)
        if country:
            stmt = stmt.where(ServiceProfile.country == country)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create(self, **kwargs) -> ServiceProfile:
        profile = ServiceProfile(**kwargs)
        self.session.add(profile)
        await self.session.flush()
        return profile

    async def update_fields(self, profile_id: str, **kwargs) -> ServiceProfile | None:
        profile = await self.get(profile_id)
        if not profile:
            return None
        for k, v in kwargs.items():
            if hasattr(profile, k):
                setattr(profile, k, v)
        profile.updated_at = datetime.utcnow()
        await self.session.flush()
        return profile

    async def delete(self, profile_id: str) -> bool:
        profile = await self.get(profile_id)
        if not profile:
            return False
        await self.session.delete(profile)
        await self.session.flush()
        return True

    # ── Translations ──────────────────────────────────────────────

    async def get_translation(
        self, profile_id: str, language: str
    ) -> ServiceProfileTranslation | None:
        stmt = select(ServiceProfileTranslation).where(
            ServiceProfileTranslation.profile_id == profile_id,
            ServiceProfileTranslation.language == language,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_translations(self, profile_id: str) -> list[ServiceProfileTranslation]:
        stmt = (
            select(ServiceProfileTranslation)
            .where(ServiceProfileTranslation.profile_id == profile_id)
            .order_by(ServiceProfileTranslation.language)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def upsert_translation(
        self, profile_id: str, language: str, **fields
    ) -> ServiceProfileTranslation:
        existing = await self.get_translation(profile_id, language)
        if existing:
            for k, v in fields.items():
                if hasattr(existing, k):
                    setattr(existing, k, v)
            existing.updated_at = datetime.utcnow()
            translation = existing
        else:
            translation = ServiceProfileTranslation(
                profile_id=profile_id, language=language, **fields
            )
            self.session.add(translation)
        await self.session.flush()
        # Sync embedding
        if self._embedder:
            try:
                emb_text = self._build_embed_text(translation)
                vector = await self._embedder.embed(emb_text)
                translation.embedding = vector
                await self.session.flush()
            except Exception as exc:
                log.warning("Failed to embed translation %s: %s", translation.id, exc)
        return translation

    async def delete_translation(self, profile_id: str, language: str) -> bool:
        existing = await self.get_translation(profile_id, language)
        if not existing:
            return False
        await self.session.delete(existing)
        await self.session.flush()
        return True

    @staticmethod
    def _build_embed_text(t: ServiceProfileTranslation) -> str:
        """Конкатенація всіх семантичних полів для embedding."""
        parts = []
        if t.short_description:
            parts.append(t.short_description)
        if t.detailed_description:
            parts.append(t.detailed_description)
        if t.addresses_problems:
            parts.append("Problems: " + ", ".join(t.addresses_problems))
        if t.target_audience:
            parts.append("For: " + ", ".join(t.target_audience))
        if t.benefits:
            parts.append("Benefits: " + ", ".join(t.benefits))
        if t.keywords:
            parts.append("Keywords: " + ", ".join(t.keywords))
        if t.sales_pitch:
            parts.append(t.sales_pitch)
        return " | ".join(parts)

    # ── Vector search by client concern ───────────────────────────

    async def search_by_concern(
        self,
        query: str,
        language: str = "uk",
        country: str | None = None,
        salon_id: str | None = None,
        limit: int = 5,
    ) -> list[tuple[ServiceProfile, ServiceProfileTranslation, float]]:
        """Hybrid search: vector cosine на translation embedding +
        filter по country + structured keyword boost.

        Повертає [(profile, translation, score), ...].
        """
        if not self._embedder:
            return []

        try:
            query_emb = await self._embedder.embed(query)
        except Exception as exc:
            log.warning("Failed to embed query for concern search: %s", exc)
            return []

        # Vector search через cosine_distance, JOIN з profile
        stmt = (
            select(
                ServiceProfileTranslation,
                ServiceProfileTranslation.embedding.cosine_distance(query_emb).label("distance"),
            )
            .join(ServiceProfile, ServiceProfile.id == ServiceProfileTranslation.profile_id)
            .where(
                ServiceProfile.enabled.is_(True),
                ServiceProfileTranslation.language == language,
                ServiceProfileTranslation.embedding.is_not(None),
            )
        )
        if country:
            stmt = stmt.where(ServiceProfile.country == country)
        stmt = stmt.order_by("distance").limit(limit * 3)

        rows = (await self.session.execute(stmt)).all()

        # Збираємо результати
        results = []
        query_lower = query.lower()
        for translation, distance in rows:
            profile = await self.get(translation.profile_id)
            if not profile:
                continue
            if salon_id and salon_id not in (profile.salon_ids or []):
                continue
            # Базовий score = 1 - cosine distance (вищий = краще)
            base_score = 1.0 - float(distance)
            # Keyword boost: рідкісні точні збіги
            kw_boost = 0.0
            for kw in (translation.keywords or []):
                if kw.lower() in query_lower:
                    kw_boost += 0.05
            # Boost за adresses_problems точний збіг
            for problem in (translation.addresses_problems or []):
                if problem.lower() in query_lower or query_lower in problem.lower():
                    kw_boost += 0.1
            score = base_score + kw_boost
            results.append((profile, translation, score))

        results.sort(key=lambda r: r[2], reverse=True)
        return results[:limit]

    # ── Search by concern (PROFILE-LEVEL) ─────────────────────────

    async def search_by_concern_v2(
        self,
        query: str,
        country: str | None = None,
        salon_id: str | None = None,
        limit: int = 5,
        language: str = "uk",
    ) -> list[dict]:
        """Profile-level vector search через translation.embedding.
        Options layer виключено — все на profile."""
        if not self._embedder:
            return []

        try:
            query_emb = await self._embedder.embed(query)
        except Exception as exc:
            log.warning("Failed to embed query: %s", exc)
            return []

        # Шукаємо по translation embeddings (UK content)
        stmt = (
            select(
                ServiceProfile,
                ServiceProfileTranslation,
                ServiceProfileTranslation.embedding.cosine_distance(query_emb).label("distance"),
            )
            .join(ServiceProfile, ServiceProfile.id == ServiceProfileTranslation.profile_id)
            .where(
                ServiceProfile.enabled.is_(True),
                ServiceProfileTranslation.language == "uk",
                ServiceProfileTranslation.embedding.is_not(None),
            )
        )
        if country:
            stmt = stmt.where(ServiceProfile.country == country)
        stmt = stmt.order_by("distance").limit(limit * 3)

        rows = (await self.session.execute(stmt)).all()
        results: list[dict] = []
        query_lower = query.lower()

        for profile, translation, distance in rows:
            if salon_id and salon_id not in (profile.salon_ids or []):
                continue
            base_score = 1.0 - float(distance)
            # Keyword boost мовою клієнта
            kw_boost = 0.0
            lang_keywords = (profile.keywords_by_lang or {}).get(language) or (translation.keywords or [])
            for kw in lang_keywords:
                if kw.lower() in query_lower:
                    kw_boost += 0.05
            for problem in (translation.addresses_problems or []):
                if problem.lower() in query_lower or query_lower in problem.lower():
                    kw_boost += 0.1
            score = base_score + kw_boost

            results.append({
                "type": "profile",
                "category": profile.name,
                "category_id": profile.id,
                "short_description": translation.short_description,
                "addresses_problems": list(translation.addresses_problems or []),
                "target_audience": list(translation.target_audience or []),
                "benefits": list(translation.benefits or []),
                "sales_pitch": translation.sales_pitch,
                "cross_sell": list(translation.cross_sell or []),
                "procedure_steps": list(translation.procedure_steps or []),
                "contraindications": list(translation.contraindications or []),
                "aftercare_advice": translation.aftercare_advice,
                "canonical_keys": list(profile.canonical_keys or []),
                "key_descriptions": dict(profile.key_descriptions or {}),
                "score": round(score, 3),
            })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:limit]

    # ── Linked services ───────────────────────────────────────────

    async def services_for_profile(
        self, profile_id: str, country: str
    ) -> list[Service]:
        """Знаходить всі послуги які цей профіль покриває (за canonical_key)
        мінус виключені через override.

        ВАЖЛИВО: Service модель зі схеми country, потрібна country_session.
        Тому цей метод приймає country явно.
        """
        profile = await self.get(profile_id)
        if not profile:
            return []

        # Excluded service ids
        excluded_stmt = select(ServiceProfileOverride.service_id).where(
            ServiceProfileOverride.profile_id == profile_id,
            ServiceProfileOverride.country == country,
            ServiceProfileOverride.excluded.is_(True),
        )
        excluded = set((await self.session.execute(excluded_stmt)).scalars().all())

        # Auto-match by canonical_key (cross-schema)
        # Note: in current session search_path is public; need raw SQL or different session
        # We use raw SQL for this:
        from sqlalchemy import text as sql_text

        sql = sql_text(
            f"""
            SELECT id, salon_id, name, name_uk, name_ru, name_en, name_pl,
                   duration_min, price, price_currency, gender, archive
            FROM {country}.service
            WHERE canonical_key = :key AND archive = false
            ORDER BY name
            """
        )
        rows = await self.session.execute(sql, {"key": profile.canonical_key})
        result = []
        for row in rows.fetchall():
            if row[0] in excluded:
                continue
            # Створюємо легковагий dict замість Service model bound to schema
            result.append({
                "id": row[0],
                "salon_id": row[1],
                "name": row[2],
                "name_uk": row[3],
                "name_ru": row[4],
                "name_en": row[5],
                "name_pl": row[6],
                "duration_min": row[7],
                "price": float(row[8]) if row[8] else 0,
                "currency": row[9],
                "gender": row[10],
                "archive": row[11],
            })
        return result

    async def count_services_for_canonical_key(
        self, canonical_key: str, country: str | None = None
    ) -> int:
        """Підраховує скільки послуг матчиться на канонічний ключ (для preview в UI)."""
        from sqlalchemy import text as sql_text

        countries = [country] if country else ["ua", "pl", "gb"]
        total = 0
        for c in countries:
            sql = sql_text(
                f"SELECT COUNT(*) FROM {c}.service WHERE canonical_key = :key AND archive = false"
            )
            row = await self.session.execute(sql, {"key": canonical_key})
            total += row.scalar() or 0
        return total

    # ── Variants (A/B testing) ────────────────────────────────────

    async def list_variants(
        self, profile_id: str, language: str | None = None
    ) -> list[ServiceProfileVariant]:
        stmt = select(ServiceProfileVariant).where(
            ServiceProfileVariant.profile_id == profile_id
        )
        if language:
            stmt = stmt.where(ServiceProfileVariant.language == language)
        stmt = stmt.order_by(ServiceProfileVariant.label)
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_variant(self, variant_id: str) -> ServiceProfileVariant | None:
        stmt = select(ServiceProfileVariant).where(ServiceProfileVariant.id == variant_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create_variant(self, **kwargs) -> ServiceProfileVariant:
        variant = ServiceProfileVariant(**kwargs)
        self.session.add(variant)
        await self.session.flush()
        return variant

    async def update_variant(self, variant_id: str, **kwargs) -> ServiceProfileVariant | None:
        variant = await self.get_variant(variant_id)
        if not variant:
            return None
        for k, v in kwargs.items():
            if hasattr(variant, k):
                setattr(variant, k, v)
        variant.updated_at = datetime.utcnow()
        await self.session.flush()
        return variant

    async def delete_variant(self, variant_id: str) -> bool:
        variant = await self.get_variant(variant_id)
        if not variant:
            return False
        await self.session.delete(variant)
        await self.session.flush()
        return True

    async def select_active_variant(
        self, profile_id: str, language: str
    ) -> ServiceProfileVariant | None:
        """Обирає варіант для показу клієнту (weighted random на active variants)."""
        variants = await self.list_variants(profile_id, language)
        active = [v for v in variants if v.status == "active" and v.weight > 0]
        if not active:
            return None
        weights = [v.weight for v in active]
        return random.choices(active, weights=weights, k=1)[0]

    async def track_event(
        self,
        variant_id: str,
        event_type: str,
        conversation_id: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Записує імпресію або конверсію + інкрементує лічильник варіанту."""
        event = ServiceProfileVariantEvent(
            variant_id=variant_id,
            event_type=event_type,
            conversation_id=conversation_id,
            metadata_=metadata,
        )
        self.session.add(event)
        if event_type == "impression":
            await self.session.execute(
                update(ServiceProfileVariant)
                .where(ServiceProfileVariant.id == variant_id)
                .values(impressions=ServiceProfileVariant.impressions + 1)
            )
        elif event_type == "conversion":
            await self.session.execute(
                update(ServiceProfileVariant)
                .where(ServiceProfileVariant.id == variant_id)
                .values(conversions=ServiceProfileVariant.conversions + 1)
            )
        await self.session.flush()

    # ── Versions (versioning + rollback) ──────────────────────────

    async def list_versions(self, profile_id: str) -> list[ServiceProfileVersion]:
        stmt = (
            select(ServiceProfileVersion)
            .where(ServiceProfileVersion.profile_id == profile_id)
            .order_by(ServiceProfileVersion.version_number.desc())
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def save_version(
        self,
        profile_id: str,
        change_summary: str | None = None,
        created_by: str | None = None,
    ) -> ServiceProfileVersion | None:
        """Створює снапшот поточного стану профілю + усіх перекладів + варіантів."""
        profile = await self.get(profile_id)
        if not profile:
            return None
        translations = await self.list_translations(profile_id)
        variants = await self.list_variants(profile_id)

        snapshot = {
            "profile": {
                "name": profile.name,
                "canonical_key": profile.canonical_key,
                "country": profile.country,
                "enabled": profile.enabled,
                "default_language": profile.default_language,
            },
            "translations": [
                {
                    "language": t.language,
                    "short_description": t.short_description,
                    "detailed_description": t.detailed_description,
                    "addresses_problems": t.addresses_problems,
                    "target_audience": t.target_audience,
                    "benefits": t.benefits,
                    "keywords": t.keywords,
                    "procedure_steps": t.procedure_steps,
                    "contraindications": t.contraindications,
                    "aftercare_advice": t.aftercare_advice,
                    "cross_sell": t.cross_sell,
                    "duration_typical_min": t.duration_typical_min,
                    "sales_pitch": t.sales_pitch,
                }
                for t in translations
            ],
            "variants": [
                {
                    "language": v.language,
                    "label": v.label,
                    "short_description": v.short_description,
                    "sales_pitch": v.sales_pitch,
                    "addresses_problems": v.addresses_problems,
                    "benefits": v.benefits,
                    "keywords": v.keywords,
                    "weight": v.weight,
                    "status": v.status,
                }
                for v in variants
            ],
        }

        next_version = (profile.current_version or 0) + 1
        version = ServiceProfileVersion(
            profile_id=profile_id,
            version_number=next_version,
            snapshot=snapshot,
            change_summary=change_summary,
            created_by=created_by,
        )
        self.session.add(version)
        profile.current_version = next_version
        await self.session.flush()
        return version

    async def rollback_to_version(
        self, profile_id: str, version_number: int, actor: str | None = None
    ) -> bool:
        """Відкочує профіль до конкретної версії. Створює нову версію зі снапшотом."""
        stmt = select(ServiceProfileVersion).where(
            ServiceProfileVersion.profile_id == profile_id,
            ServiceProfileVersion.version_number == version_number,
        )
        version = (await self.session.execute(stmt)).scalar_one_or_none()
        if not version:
            return False

        snapshot = version.snapshot
        profile = await self.get(profile_id)
        if not profile:
            return False

        # Restore profile fields
        for k, v in snapshot.get("profile", {}).items():
            if hasattr(profile, k):
                setattr(profile, k, v)

        # Drop existing translations and recreate
        existing_translations = await self.list_translations(profile_id)
        for t in existing_translations:
            await self.session.delete(t)
        await self.session.flush()

        for t_data in snapshot.get("translations", []):
            await self.upsert_translation(profile_id, **t_data)

        # Drop existing variants and recreate
        existing_variants = await self.list_variants(profile_id)
        for v in existing_variants:
            await self.session.delete(v)
        await self.session.flush()

        for v_data in snapshot.get("variants", []):
            self.session.add(ServiceProfileVariant(profile_id=profile_id, **v_data))

        await self.session.flush()
        # Save a new version marking the rollback
        await self.save_version(
            profile_id,
            change_summary=f"Rollback to version {version_number}",
            created_by=actor,
        )
        return True

    # ── Override (manual link control) ────────────────────────────

    async def list_overrides(self, profile_id: str) -> list[ServiceProfileOverride]:
        stmt = select(ServiceProfileOverride).where(
            ServiceProfileOverride.profile_id == profile_id
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def add_override(
        self, profile_id: str, service_id: str, country: str, excluded: bool = True
    ) -> ServiceProfileOverride:
        override = ServiceProfileOverride(
            profile_id=profile_id,
            service_id=service_id,
            country=country,
            excluded=excluded,
        )
        self.session.add(override)
        await self.session.flush()
        return override

    async def remove_override(
        self, profile_id: str, service_id: str, country: str
    ) -> bool:
        stmt = select(ServiceProfileOverride).where(
            ServiceProfileOverride.profile_id == profile_id,
            ServiceProfileOverride.service_id == service_id,
            ServiceProfileOverride.country == country,
        )
        existing = (await self.session.execute(stmt)).scalar_one_or_none()
        if not existing:
            return False
        await self.session.delete(existing)
        await self.session.flush()
        return True

    # ── Stats для dashboard ───────────────────────────────────────

    async def stats(self) -> dict[str, Any]:
        rows = (await self.session.execute(select(ServiceProfile))).scalars().all()
        rows = list(rows)
        translations = (
            await self.session.execute(select(func.count()).select_from(ServiceProfileTranslation))
        ).scalar() or 0
        variants = (
            await self.session.execute(select(func.count()).select_from(ServiceProfileVariant))
        ).scalar() or 0
        return {
            "total": len(rows),
            "enabled": sum(1 for r in rows if r.enabled),
            "translations": int(translations),
            "variants": int(variants),
        }

    # ── Coverage statistics ───────────────────────────────────────

    async def coverage_stats(self) -> dict[str, Any]:
        """Підраховує покриття послуг профілями.

        Повертає:
        - per_country: {ua: {total, with_profile, without_profile, coverage_pct}, ...}
        - canonical_keys: {ua: {total, with_profile, without_profile}, ...}
        """
        from sqlalchemy import text as sql_text

        # Усі canonical_keys що мають профіль:
        # 1) прямі profile.canonical_key
        # 2) ключі через option.canonical_keys[] (family-options покривають більшість)
        rows = await self.session.execute(
            select(ServiceProfile.canonical_key).where(
                ServiceProfile.canonical_key.isnot(None)
            )
        )
        keys_with_profile = {row[0] for row in rows.fetchall() if row[0]}

        # Канонічні ключі через profile.canonical_keys[]
        profile_keys_rows = await self.session.execute(sql_text(
            "SELECT jsonb_array_elements_text(canonical_keys::jsonb) AS k "
            "FROM public.service_profile WHERE canonical_keys IS NOT NULL"
        ))
        for row in profile_keys_rows.fetchall():
            if row[0]:
                keys_with_profile.add(row[0])

        result: dict[str, Any] = {
            "per_country": {},
            "canonical_keys": {},
            "global": {
                "total_services": 0,
                "services_with_profile": 0,
                "services_without_profile": 0,
                "total_canonical_keys": 0,
                "canonical_keys_with_profile": 0,
                "canonical_keys_without_profile": 0,
            },
        }

        for country in ("ua", "pl", "gb"):
            # Усі канонічні ключі цієї країни (з кількостями послуг)
            sql = sql_text(
                f"""
                SELECT canonical_key, COUNT(*) as svc_count
                FROM {country}.service
                WHERE archive = false AND canonical_key IS NOT NULL
                GROUP BY canonical_key
                """
            )
            country_rows = await self.session.execute(sql)
            country_keys: dict[str, int] = {}
            for row in country_rows.fetchall():
                country_keys[row[0]] = row[1]

            total_services = sum(country_keys.values())
            with_profile_keys = set(country_keys.keys()) & keys_with_profile
            without_profile_keys = set(country_keys.keys()) - keys_with_profile

            services_with = sum(country_keys[k] for k in with_profile_keys)
            services_without = sum(country_keys[k] for k in without_profile_keys)

            result["per_country"][country] = {
                "total": total_services,
                "with_profile": services_with,
                "without_profile": services_without,
                "coverage_pct": round(services_with / total_services * 100, 1)
                if total_services > 0 else 0,
            }
            result["canonical_keys"][country] = {
                "total": len(country_keys),
                "with_profile": len(with_profile_keys),
                "without_profile": len(without_profile_keys),
            }

            result["global"]["total_services"] += total_services
            result["global"]["services_with_profile"] += services_with
            result["global"]["services_without_profile"] += services_without

        # Глобальні унікальні keys (можуть бути в кількох countries)
        all_keys_sql = sql_text(
            """
            SELECT DISTINCT canonical_key FROM (
                SELECT canonical_key FROM ua.service WHERE archive = false AND canonical_key IS NOT NULL
                UNION
                SELECT canonical_key FROM pl.service WHERE archive = false AND canonical_key IS NOT NULL
                UNION
                SELECT canonical_key FROM gb.service WHERE archive = false AND canonical_key IS NOT NULL
            ) all_keys
            """
        )
        all_keys = {row[0] for row in (await self.session.execute(all_keys_sql)).fetchall()}
        result["global"]["total_canonical_keys"] = len(all_keys)
        result["global"]["canonical_keys_with_profile"] = len(all_keys & keys_with_profile)
        result["global"]["canonical_keys_without_profile"] = len(all_keys - keys_with_profile)
        result["global"]["coverage_pct"] = (
            round(
                result["global"]["services_with_profile"]
                / result["global"]["total_services"]
                * 100,
                1,
            )
            if result["global"]["total_services"] > 0
            else 0
        )

        return result

    async def list_missing_canonical_keys(
        self,
        country: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 200,
    ) -> dict[str, Any]:
        """Список canonical_keys які НЕ мають профілю — з pagination.

        Повертає:
        {
            items: [{canonical_key, sample_name, total_services, by_country: {...}}, ...],
            total: int,           # загальна кількість після фільтрів
            page: int,
            page_size: int,
            total_pages: int,
        }
        Сортовано за total_services DESC — найбільш популярні зверху.
        """
        from sqlalchemy import text as sql_text

        # Усі ключі що мають профіль (виключити з результату):
        # 1) Прямі profile.canonical_key
        # 2) Ключі через option.canonical_keys[] — family профілі покривають десятки/сотні
        #    канонічних ключів через списки в опціях; це основний механізм 100% покриття.
        rows = await self.session.execute(
            select(ServiceProfile.canonical_key).where(
                ServiceProfile.canonical_key.isnot(None)
            )
        )
        keys_with_profile = {row[0] for row in rows.fetchall() if row[0]}

        # Канонічні ключі через profile.canonical_keys[]
        profile_keys_rows = await self.session.execute(sql_text(
            "SELECT jsonb_array_elements_text(canonical_keys::jsonb) AS k "
            "FROM public.service_profile WHERE canonical_keys IS NOT NULL"
        ))
        for row in profile_keys_rows.fetchall():
            if row[0]:
                keys_with_profile.add(row[0])

        # Збираємо canonical_keys по всіх countries з sample name
        countries = [country] if country else ["ua", "pl", "gb"]
        aggregated: dict[str, dict[str, Any]] = {}

        from app.domain.services.canonical_key import normalize_to_canonical_key

        for c in countries:
            sql = sql_text(
                f"""
                SELECT canonical_key,
                       COUNT(*) as svc_count,
                       (array_agg(COALESCE(name_uk, name) ORDER BY name))[1] as sample_name
                FROM {c}.service
                WHERE archive = false
                  AND canonical_key IS NOT NULL
                GROUP BY canonical_key
                """
            )
            for row in (await self.session.execute(sql)).fetchall():
                key, count, sample = row[0], row[1], row[2]
                if key in keys_with_profile:
                    continue
                if key not in aggregated:
                    # Чистимо sample_name від довжини/рівня для зручного перегляду
                    from app.domain.services.canonical_key import (
                        _ADDON_RE,
                        _LENGTH_PATTERNS,
                        _LEVEL_RE,
                    )
                    clean_name = sample or ""
                    clean_name = _ADDON_RE.sub(" ", clean_name)
                    for pat in _LENGTH_PATTERNS:
                        clean_name = pat.sub(" ", clean_name)
                    while True:
                        new = _LEVEL_RE.sub("", clean_name).strip()
                        if new == clean_name:
                            break
                        clean_name = new
                    import re as _re
                    clean_name = _re.sub(r"\s+", " ", clean_name).strip()

                    aggregated[key] = {
                        "canonical_key": key,
                        "sample_name": clean_name or sample,
                        "total_services": 0,
                        "by_country": {"ua": 0, "pl": 0, "gb": 0},
                    }
                aggregated[key]["by_country"][c] = count
                aggregated[key]["total_services"] += count

        items = list(aggregated.values())

        # Filter by search — тільки по назві послуги (sample_name), не по canonical_key
        if search:
            term = search.lower()
            items = [
                it
                for it in items
                if term in (it["sample_name"] or "").lower()
            ]

        # Sort by total_services DESC
        items.sort(key=lambda x: x["total_services"], reverse=True)

        total = len(items)
        page = max(1, page)
        page_size = max(1, min(page_size, 1000))
        total_pages = max(1, (total + page_size - 1) // page_size)
        # Clamp page до total_pages
        page = min(page, total_pages)
        offset = (page - 1) * page_size
        page_items = items[offset : offset + page_size]

        return {
            "items": page_items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }
