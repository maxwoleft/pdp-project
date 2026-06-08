"""Merge profile translation defaults з per-ckey overrides.

Industry pattern (Stripe Product/Price, Shopify Product/Variant):
  profile = shared concept, общий для всіх variants
  ckey = variant з власною discriminator-інформацією

Поля типу addresses_problems/target_audience/benefits/keywords/sales_pitch/
cross_sell/procedure_steps/contraindications/aftercare_advice мають defaults
у translation; per-ckey override опціональний.

Правило resolve:
  override[field] truthy → override.
  Інакше → translation default.

Це дозволяє один profile тримати наприклад "Фарбування Balmain" з ckeys:
  - farbuvannya: default concerns (зміна іміджу, сивина)
  - farbuvannya_korin: override concerns (відросли корені — інший concern)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


OVERRIDABLE_FIELDS = (
    "addresses_problems",
    "target_audience",
    "benefits",
    "keywords",
    "sales_pitch",
    "cross_sell",
    "procedure_steps",
    "contraindications",
    "aftercare_advice",
    "short_description",
    "detailed_description",
    "duration_typical_min",
)


@dataclass(frozen=True, slots=True)
class ResolvedProfileView:
    """View profile data для конкретного ckey — merge defaults + override."""
    profile_id: str
    profile_name: str
    canonical_key: str
    language: str
    short_description: str
    detailed_description: str | None
    addresses_problems: list[str]
    target_audience: list[str]
    benefits: list[str]
    keywords: list[str]
    procedure_steps: list[str]
    contraindications: list[str]
    aftercare_advice: str | None
    cross_sell: list[str]
    duration_typical_min: int | None
    sales_pitch: str | None
    overridden_fields: tuple[str, ...]


def _is_truthy(v: Any) -> bool:
    """True якщо value є substantive override (не null, не порожній)."""
    if v is None:
        return False
    if isinstance(v, (list, dict, str)) and len(v) == 0:
        return False
    return True


def resolve_for_ckey(translation, canonical_key: str) -> ResolvedProfileView:
    """Merge translation default fields з ckey-override.

    `translation` — ServiceProfileTranslation row (або dict-like).
    """
    if hasattr(translation, "ckey_overrides"):
        overrides_all = dict(translation.ckey_overrides or {})
    else:
        overrides_all = dict(translation.get("ckey_overrides") or {})
    override = dict(overrides_all.get(canonical_key) or {})

    def _get_default(field: str) -> Any:
        if hasattr(translation, field):
            return getattr(translation, field)
        return translation.get(field) if isinstance(translation, dict) else None

    resolved: dict[str, Any] = {}
    overridden: list[str] = []
    for field in OVERRIDABLE_FIELDS:
        ov = override.get(field)
        if _is_truthy(ov):
            resolved[field] = ov
            overridden.append(field)
        else:
            resolved[field] = _get_default(field)

    profile = getattr(translation, "profile", None)
    profile_id = (
        getattr(profile, "id", None)
        or getattr(translation, "profile_id", None)
        or (translation.get("profile_id") if isinstance(translation, dict) else None)
    )
    profile_name = (
        getattr(profile, "name", None)
        or (translation.get("profile_name") if isinstance(translation, dict) else None)
        or ""
    )
    language = (
        getattr(translation, "language", None)
        or (translation.get("language") if isinstance(translation, dict) else "uk")
    )

    return ResolvedProfileView(
        profile_id=str(profile_id or ""),
        profile_name=profile_name,
        canonical_key=canonical_key,
        language=language,
        short_description=resolved.get("short_description") or "",
        detailed_description=resolved.get("detailed_description"),
        addresses_problems=list(resolved.get("addresses_problems") or []),
        target_audience=list(resolved.get("target_audience") or []),
        benefits=list(resolved.get("benefits") or []),
        keywords=list(resolved.get("keywords") or []),
        procedure_steps=list(resolved.get("procedure_steps") or []),
        contraindications=list(resolved.get("contraindications") or []),
        aftercare_advice=resolved.get("aftercare_advice"),
        cross_sell=list(resolved.get("cross_sell") or []),
        duration_typical_min=resolved.get("duration_typical_min"),
        sales_pitch=resolved.get("sales_pitch"),
        overridden_fields=tuple(overridden),
    )
