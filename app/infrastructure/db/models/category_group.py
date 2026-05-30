"""Моделі для admin-керованих груп категорій.

Адмін групує CRM-категорії у власні групи (Group Categories), щоб потім
зручно обʼєднувати канонічні ключі в ServiceProfile.

Прив'язка members по category.id (per-salon) — стійко до перейменування CRM.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base


class CategoryGroup(Base):
    __tablename__ = "category_group"
    __table_args__ = (
        CheckConstraint("group_level BETWEEN 1 AND 3", name="ck_category_group_level"),
        {"schema": "public"},
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_group_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("public.category_group.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    group_level: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    members: Mapped[list["GroupMember"]] = relationship(
        "GroupMember", back_populates="group", cascade="all, delete-orphan"
    )
    parent: Mapped["CategoryGroup | None"] = relationship(
        "CategoryGroup", remote_side="CategoryGroup.id", backref="children"
    )


class GroupMember(Base):
    __tablename__ = "group_member"
    __table_args__ = (
        UniqueConstraint("group_id", "member_type", "member_id", name="uq_group_member"),
        CheckConstraint(
            "member_type IN ('parent_category', 'subcategory', 'canonical_key')",
            name="ck_group_member_type",
        ),
        {"schema": "public"},
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    group_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("public.category_group.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    member_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # category.id (per-salon, format "{salon_id}:{crm_id}") OR canonical_key (slug)
    member_id: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    group: Mapped[CategoryGroup] = relationship(back_populates="members")
