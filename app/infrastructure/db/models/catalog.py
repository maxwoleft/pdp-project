"""Каталог: категорії, позиції (спеціалізації), послуги, товари.

Country-specific. Створюються в схемі кожної країни (ua/pl/gb).
"""
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.base import Base

EMBEDDING_DIM = 1536  # text-embedding-3-small


class Category(Base):
    """Ієрархія категорій послуг (HAIR → Colouring → Highlight techniques).

    Прив'язана до конкретного салону: у кожного салону свій набір категорій.
    """
    __tablename__ = "category"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    salon_id: Mapped[str] = mapped_column(String(80), ForeignKey("salon.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(String(80), ForeignKey("category.id"), nullable=True)
    picture: Mapped[str | None] = mapped_column(String(500), nullable=True)
    archive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    parent: Mapped["Category | None"] = relationship(
        "Category", remote_side="Category.id", back_populates="children"
    )
    children: Mapped[list["Category"]] = relationship(
        "Category", back_populates="parent"
    )


class Position(Base):
    """Спеціалізація майстра (Nail Technician, Colorist, Hair Stylist...).

    Прив'язана до Service і до Employee. Це не job title, а саме скіл/спеціалізація,
    яка визначає, які послуги майстер може виконувати.
    """
    __tablename__ = "position"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    salon_id: Mapped[str] = mapped_column(String(80), ForeignKey("salon.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    archive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Service(Base):
    """Послуга конкретного салону.

    Зв'язок з майстрами — через position: майстер виконує послугу, якщо
    service.position_id присутній у його employee.positions.
    """
    __tablename__ = "service"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    salon_id: Mapped[str] = mapped_column(String(80), ForeignKey("salon.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(1000), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_plain: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    price_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    gender: Mapped[str] = mapped_column(String(10), nullable=False, default="both")  # male/female/both

    category_id: Mapped[str] = mapped_column(String(80), ForeignKey("category.id"), nullable=False)
    position_id: Mapped[str] = mapped_column(String(80), ForeignKey("position.id"), nullable=False)

    archive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ── Переклади назви (заповнюються при sync з CRM) ────────────
    name_uk: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    name_ru: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    name_en: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    name_pl: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # ── Canonical key для linking з ServiceProfile ───────────────
    # Нормалізована форма назви без довжини/рівня/салону/бренду/обʼєму.
    # Багато конкретних послуг → один canonical_key → один профіль.
    canonical_key: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    # ── Структуровані атрибути (витягуються з назви через extract_attributes) ──
    # Усі nullable: nullable=True означає "не вказано в назві CRM".
    # Дозволяють фільтрувати у пошуку: один canonical_key + brand=... + volume_ml=...
    brand: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    volume_ml: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    zones: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    session_minutes: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    ampules: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    # Семантичний embedding конкатенації всіх мовних варіантів:
    # "uk назва | ru назва | en назва | pl назва" → vector
    name_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )

    category: Mapped["Category"] = relationship("Category")
    position: Mapped["Position"] = relationship("Position")


class ProductCategory(Base):
    """Окрема таксономія для товарів (роздріб, бренди косметики). Per-salon."""
    __tablename__ = "product_category"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    salon_id: Mapped[str] = mapped_column(String(80), ForeignKey("salon.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    archive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Product(Base):
    """Товари роздрібного продажу. Per-salon."""
    __tablename__ = "product"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    salon_id: Mapped[str] = mapped_column(String(80), ForeignKey("salon.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(50), nullable=True)
    vendor_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    units: Mapped[str] = mapped_column(String(20), default="pcs")
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    price_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    category_id: Mapped[str | None] = mapped_column(
        String(80), ForeignKey("product_category.id"), nullable=True
    )
    archive: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    category: Mapped["ProductCategory | None"] = relationship("ProductCategory")
