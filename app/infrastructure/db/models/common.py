"""Спільні довідники у схемі public."""
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class Country(Base):
    __tablename__ = "country"
    __table_args__ = {"schema": "public"}

    code: Mapped[str] = mapped_column(String(2), primary_key=True)  # ua, pl, gb
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), nullable=False)  # Europe/Kyiv
    currency: Mapped[str] = mapped_column(String(3), nullable=False)   # UAH, PLN, GBP
    default_lang: Mapped[str] = mapped_column(String(5), nullable=False)  # uk, pl, en


class CountryMessenger(Base):
    """Месенджер-аккаунт країни.

    Один аккаунт обслуговує ВСІ салони країни. Salon обирається в межах діалогу
    через `list_salons` (агент пропонує клієнту, той обирає).

    Одна країна може мати кілька аккаунтів одного каналу (наприклад два TG-боти
    для бренд-розмежування), і всі канали окремо.

    credentials (jsonb) — бекенд-специфічно:
      telegram:  {"bot_token": "...", "bot_username": "..."}
      whatsapp:  {"phone_number_id": "...", "access_token": "...", "verify_token": "..."}
      instagram: {"page_id": "...", "access_token": "..."}
      facebook:  {"page_id": "...", "access_token": "..."}
      viber:     {"auth_token": "...", "bot_name": "..."}
    """
    __tablename__ = "country_messenger"
    __table_args__ = (
        UniqueConstraint("channel", "external_account_id", name="uq_country_messenger_account"),
        {"schema": "public"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    country_code: Mapped[str] = mapped_column(
        String(2), ForeignKey("public.country.code"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    external_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    credentials: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    webhook_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
