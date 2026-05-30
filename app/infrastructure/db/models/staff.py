"""Салони, працівники, M:N зв'язок з позиціями."""
from sqlalchemy import JSON, Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base


class Salon(Base):
    """Конкретний салон у місті."""
    __tablename__ = "salon"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    timezone: Mapped[str] = mapped_column(String(50), nullable=False)
    working_hours: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {"mon": "09:00-21:00", ...}
    archive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

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
    prepayment_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    archive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    salon_id: Mapped[str] = mapped_column(String(80), ForeignKey("salon.id"), nullable=False)
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
