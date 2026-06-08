"""Салони, працівники, M:N зв'язок з позиціями.

Salon живе у глобальній схемі `booking` (а не per-country) — це єдине джерело
правди для всіх 18 салонів мережі. AI Chat country-specific таблиці
(employee, category, service, ...) FK-яться на booking.salons cross-schema.
"""
from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base


class Salon(Base):
    """Конкретний салон у місті. Глобальний — живе у booking.salons."""
    __tablename__ = "salons"
    __table_args__ = {"schema": "booking"}

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True)  # ua/pl/gb
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    location_slug: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="planned", index=True)
    timezone: Mapped[str] = mapped_column(String(50), nullable=False)
    archive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    database_code: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    data_dir: Mapped[str | None] = mapped_column(String(255), nullable=True)
    working_hours: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # ── booking-presentation поля (раніше жили в MySQL pdpbooking.salons) ──
    address_line: Mapped[str | None] = mapped_column(String(500), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    phone_display: Mapped[str | None] = mapped_column(String(50), nullable=True)
    phone_link: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payment_location_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    image: Mapped[str | None] = mapped_column(String(255), nullable=True)
    area_icon: Mapped[str | None] = mapped_column(String(255), nullable=True)
    map_embed_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    map_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    telegram_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    languages: Mapped[list | None] = mapped_column(JSON, nullable=True)
    menu_links: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    payment_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    payment_system_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Contact / display fields (editable in admin)
    whatsapp_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    instagram_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    hours_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    hours_value: Mapped[str | None] = mapped_column(String(100), nullable=True)
    smtp_from_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ai_chat_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    employees: Mapped[list["Employee"]] = relationship("Employee", back_populates="salon")


class Employee(Base):
    """Майстер салону."""
    __tablename__ = "employee"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phones: Mapped[list | None] = mapped_column(JSON, nullable=True)
    emails: Mapped[list | None] = mapped_column(JSON, nullable=True)
    roles: Mapped[list | None] = mapped_column(JSON, nullable=True)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo: Mapped[str | None] = mapped_column(String(500), nullable=True)
    prepayment_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    archive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    salon_id: Mapped[str] = mapped_column(String(80), ForeignKey("booking.salons.id"), nullable=False)
    salon: Mapped["Salon"] = relationship("Salon", back_populates="employees")

    positions: Mapped[list["EmployeePosition"]] = relationship(
        "EmployeePosition", back_populates="employee", cascade="all, delete-orphan"
    )


class EmployeePosition(Base):
    """M:N: майстер ↔ position (спеціалізація)."""
    __tablename__ = "employee_position"

    employee_id: Mapped[str] = mapped_column(String(80), ForeignKey("employee.id"), primary_key=True)
    position_id: Mapped[str] = mapped_column(String(80), ForeignKey("position.id"), primary_key=True)

    employee: Mapped["Employee"] = relationship("Employee", back_populates="positions")
