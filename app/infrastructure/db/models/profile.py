"""Service Profile + multilang + A/B + versioning models.

Архітектура:
    ServiceProfile — основний концепт, прив'язаний до canonical_key
        ├── ServiceProfileTranslation — переклади на різні мови (uk/ru/en/pl)
        ├── ServiceProfileVariant — A/B варіанти контенту з лічильниками
        │   └── ServiceProfileVariantEvent — імпресії/конверсії
        ├── ServiceProfileVersion — історія змін для rollback
        └── ServiceProfileOverride — ручний контроль auto-link

Мульти-сервісний зв'язок: один профіль → багато service.id через canonical_key.
"""
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base


class ServiceProfile(Base):
    """Канонічний профіль послуги (концепт). Один на канонічну послугу в межах country."""
    __tablename__ = "service_profile"
    __table_args__ = (
        UniqueConstraint("country", "canonical_key", name="uq_profile_country_canonical_key"),
        {"schema": "public"},
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    canonical_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    salon_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    cities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # Profile-level (винесене з options)
    canonical_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    key_descriptions: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    keywords_by_lang: Mapped[dict[str, list[str]]] = mapped_column(JSON, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    default_language: Mapped[str] = mapped_column(String(5), nullable=False, default="uk")
    active_variant_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    translations: Mapped[list["ServiceProfileTranslation"]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    variants: Mapped[list["ServiceProfileVariant"]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ServiceProfileTranslation(Base):
    """Переклад профілю на конкретну мову. Має власний embedding."""
    __tablename__ = "service_profile_translation"
    __table_args__ = (
        UniqueConstraint("profile_id", "language", name="uq_profile_translation_lang"),
        {"schema": "public"},
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    profile_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("public.service_profile.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    language: Mapped[str] = mapped_column(String(5), nullable=False, index=True)

    short_description: Mapped[str] = mapped_column(Text, nullable=False)
    detailed_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    addresses_problems: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    target_audience: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    benefits: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    keywords: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    procedure_steps: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    contraindications: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    aftercare_advice: Mapped[str | None] = mapped_column(Text, nullable=True)
    cross_sell: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    duration_typical_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sales_pitch: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Per-ckey overrides: {"<canonical_key>": {"addresses_problems": [...],
    #   "target_audience": [...], "benefits": [...], "keywords": [...],
    #   "sales_pitch": "...", "cross_sell": [...], "procedure_steps": [...],
    #   "contraindications": [...], "aftercare_advice": "..."}}
    # Якщо overrides[ckey][field] відсутнє/null/[] → fallback на translation default.
    # Дозволяє один profile тримати кілька variants з різними concerns.
    ckey_overrides: Mapped[dict[str, dict]] = mapped_column(JSON, nullable=False, default=dict)

    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    profile: Mapped[ServiceProfile] = relationship(back_populates="translations")


class ServiceProfileVariant(Base):
    """A/B варіант контенту профілю. Окремі лічильники для трекінгу конверсії."""
    __tablename__ = "service_profile_variant"
    __table_args__ = (
        UniqueConstraint("profile_id", "language", "label", name="uq_profile_variant_label"),
        {"schema": "public"},
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    profile_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("public.service_profile.id", ondelete="CASCADE"),
        nullable=False,
    )
    language: Mapped[str] = mapped_column(String(5), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)

    short_description: Mapped[str] = mapped_column(Text, nullable=False)
    sales_pitch: Mapped[str | None] = mapped_column(Text, nullable=True)
    addresses_problems: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    benefits: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    keywords: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conversions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=50)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    profile: Mapped[ServiceProfile] = relationship(back_populates="variants")

    @property
    def conversion_rate(self) -> float:
        if self.impressions == 0:
            return 0.0
        return self.conversions / self.impressions


class ServiceProfileVariantEvent(Base):
    """Подія імпресії/конверсії варіанту."""
    __tablename__ = "service_profile_variant_event"
    __table_args__ = {"schema": "public"}

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    variant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("public.service_profile_variant.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)  # impression / conversion
    conversation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class ServiceProfileVersion(Base):
    """Снапшот профілю + переклади + варіанти для rollback."""
    __tablename__ = "service_profile_version"
    __table_args__ = (
        UniqueConstraint("profile_id", "version_number", name="uq_profile_version"),
        {"schema": "public"},
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    profile_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("public.service_profile.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    change_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ServiceProfileOverride(Base):
    """Ручне керування auto-link: виключення помилково матчених послуг."""
    __tablename__ = "service_profile_override"
    __table_args__ = (
        UniqueConstraint("profile_id", "service_id", "country", name="uq_profile_override"),
        {"schema": "public"},
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    profile_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("public.service_profile.id", ondelete="CASCADE"),
        nullable=False,
    )
    service_id: Mapped[str] = mapped_column(String(36), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    excluded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
