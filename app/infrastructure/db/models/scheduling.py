"""Слоти часу, клієнти, бронювання."""
import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base


class BookingStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class TimeSlot(Base):
    """15-хвилинний слот робочого часу майстра.

    Імпортується з freetime.json. Послуга може займати кілька послідовних слотів.
    """
    __tablename__ = "time_slot"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    employee_id: Mapped[str] = mapped_column(String(80), ForeignKey("employee.id"), nullable=False, index=True)
    slot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    is_booked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    booking_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("booking.id", use_alter=True), nullable=True
    )


class Client(Base):
    """Клієнт салону. Структура повторює CRM (clients_search.json).

    id — це CRM ID з namespace по салону: f"{salon_id}:{crm_id}".
    external_ids — наше внутрішнє поле для лукапу клієнта по месенджеру:
    {"telegram": "12345", "whatsapp": "+380...", ...}.
    """
    __tablename__ = "client"

    # ── ідентифікація ─────────────────────────────────────────────
    id: Mapped[str] = mapped_column(String(80), primary_key=True)  # f"{salon_id}:{crm_id}"
    salon_id: Mapped[str] = mapped_column(String(80), ForeignKey("booking.salons.id"), nullable=False, index=True)
    crm_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)  # оригінальний UUID з CRM

    # ── профіль (з clients_search.json) ───────────────────────────
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    gender: Mapped[str | None] = mapped_column(String(20), nullable=True)
    birthday: Mapped[date | None] = mapped_column(Date, nullable=True)

    # ── фінанси ───────────────────────────────────────────────────
    balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    bonus: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)
    card_number: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # ── контакти ──────────────────────────────────────────────────
    phone: Mapped[list | None] = mapped_column(JSON, nullable=True)   # ["975379991", ...]
    email: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # ── категорії клієнта (CRM сегментація) ───────────────────────
    categories: Mapped[list | None] = mapped_column(JSON, nullable=True)        # [uuid, ...]
    categories_names: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [name, ...]

    # ── історія візитів ──────────────────────────────────────────
    first_visit: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_visit_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_visit: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_visit_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    history: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # ── інше з CRM ────────────────────────────────────────────────
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    additional_fields: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    deposit_client: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    referral_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    referral_source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)  # Potential, Active, ...
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    archive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ── наше: маппінг месенджерів ─────────────────────────────────
    # {"telegram": "123456", "whatsapp": "+380...", "instagram": "..."}
    external_ids: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    language: Mapped[str | None] = mapped_column(String(5), nullable=True)

    # ── службові ──────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class Booking(Base):
    """Запис клієнта на послугу."""
    __tablename__ = "booking"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(String(80), ForeignKey("client.id"), nullable=False, index=True)
    employee_id: Mapped[str] = mapped_column(String(80), ForeignKey("employee.id"), nullable=False)
    service_id: Mapped[str] = mapped_column(String(80), ForeignKey("service.id"), nullable=False)
    salon_id: Mapped[str] = mapped_column(String(80), ForeignKey("booking.salons.id"), nullable=False)

    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus, name="booking_status"), nullable=False, default=BookingStatus.PENDING
    )

    # ── звідки прийшов запис (для CRM-синку) ──────────────────────
    source_channel: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_chat_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── інтеграція з CRM ──────────────────────────────────────────
    crm_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)  # ID у CRM після синку
    synced_to_crm_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class ProductOrder(Base):
    """Онлайн замовлення товарів через чат-бот. Теж синкається в CRM."""
    __tablename__ = "product_order"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(String(80), ForeignKey("client.id"), nullable=False, index=True)
    salon_id: Mapped[str] = mapped_column(String(80), ForeignKey("booking.salons.id"), nullable=False)

    # позиції замовлення: [{"product_id": "...", "qty": 2, "price": 41.0}, ...]
    items: Mapped[list] = mapped_column(JSON, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    source_channel: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_chat_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    crm_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    synced_to_crm_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
