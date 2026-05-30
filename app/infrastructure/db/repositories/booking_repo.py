"""Бронювання."""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.scheduling import Booking, BookingStatus


class BookingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        client_id: int,
        employee_id: str,
        service_id: str,
        salon_id: str,
        start_at: datetime,
        end_at: datetime,
        source_channel: str | None = None,
        source_chat_id: str | None = None,
        notes: str | None = None,
    ) -> Booking:
        booking = Booking(
            client_id=client_id,
            employee_id=employee_id,
            service_id=service_id,
            salon_id=salon_id,
            start_at=start_at,
            end_at=end_at,
            status=BookingStatus.CONFIRMED,
            source_channel=source_channel,
            source_chat_id=source_chat_id,
            notes=notes,
        )
        self.session.add(booking)
        await self.session.flush()
        return booking

    async def list_for_client(self, client_id: int, only_active: bool = True) -> list[Booking]:
        stmt = select(Booking).where(Booking.client_id == client_id)
        if only_active:
            stmt = stmt.where(Booking.status.in_([BookingStatus.PENDING, BookingStatus.CONFIRMED]))
        stmt = stmt.order_by(Booking.start_at.desc())
        return list((await self.session.execute(stmt)).scalars().all())

    async def cancel(self, booking_id: int) -> Booking | None:
        booking = await self.session.get(Booking, booking_id)
        if booking:
            booking.status = BookingStatus.CANCELLED
        return booking
