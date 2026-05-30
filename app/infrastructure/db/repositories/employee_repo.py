"""Майстри. Пошук по послузі через position."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.infrastructure.db.models.catalog import Service
from app.infrastructure.db.models.staff import Employee, EmployeePosition


class EmployeeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, employee_id: str) -> Employee | None:
        return await self.session.get(Employee, employee_id)

    async def find_for_service(self, service_id: str) -> list[Employee]:
        """Майстри, які виконують послугу (мають її position) у тому ж салоні, що й послуга."""
        service = await self.session.get(Service, service_id)
        if not service:
            return []

        stmt = (
            select(Employee)
            .join(EmployeePosition, EmployeePosition.employee_id == Employee.id)
            .where(
                Employee.salon_id == service.salon_id,
                EmployeePosition.position_id == service.position_id,
                Employee.archive.is_(False),
            )
        )
        rows = await self.session.execute(stmt)
        return list(rows.scalars().unique().all())

    async def list_by_salon(self, salon_id: str) -> list[Employee]:
        rows = await self.session.execute(
            select(Employee)
            .options(selectinload(Employee.positions))
            .where(Employee.salon_id == salon_id, Employee.archive.is_(False))
        )
        return list(rows.scalars().all())
