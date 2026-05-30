"""Моделі для eval сценаріїв і admin авторизації.

Обидві в схемі public — глобальні, не country-scoped.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class EvalScenario(Base):
    """Тестовий сценарій для eval suite.

    Зберігається в БД щоб можна було редагувати через admin UI без redeploy.
    `slug` — стабільний унікальний ідентифікатор (raw text used by `--scenario` CLI).
    """
    __tablename__ = "eval_scenario"
    __table_args__ = {"schema": "public"}

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True)

    # JSON-arrays зберігаються в JSONB
    turns: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    expectations: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    must_not: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # Ground truth: ідеальні відповіді з реальних діалогів. Опційно — пусто для синтетичних.
    reference_responses: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    preset_salon_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

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


class AdminUser(Base):
    """Адмін користувач для admin panel."""
    __tablename__ = "admin_user"
    __table_args__ = {"schema": "public"}

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
