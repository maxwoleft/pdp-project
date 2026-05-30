"""Репозиторій салонів."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.staff import Salon


class SalonRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(self) -> list[Salon]:
        rows = await self.session.execute(
            select(Salon).where(Salon.archive.is_(False)).order_by(Salon.city, Salon.name)
        )
        return list(rows.scalars().all())

    async def get_by_id(self, salon_id: str) -> Salon | None:
        return await self.session.get(Salon, salon_id)

    async def list_by_city(self, city: str) -> list[Salon]:
        rows = await self.session.execute(
            select(Salon).where(Salon.city.ilike(f"%{city}%"), Salon.archive.is_(False))
        )
        return list(rows.scalars().all())
