"""Слоти часу. Знаходження вільних послідовних слотів під послугу."""
from datetime import datetime, timedelta
from math import ceil

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.scheduling import TimeSlot

SLOT_MIN = 15  # розмір базового слоту


class TimeSlotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def find_free_windows(
        self,
        employee_id: str,
        date_from: datetime,
        date_to: datetime,
        duration_min: int,
        max_results: int = 20,
    ) -> list[dict]:
        """Знаходить безперервні вікна тривалістю >= duration_min для майстра.

        Повертає список {start_at, end_at, slot_ids[]}.
        """
        slots_needed = ceil(duration_min / SLOT_MIN)

        stmt = (
            select(TimeSlot)
            .where(
                TimeSlot.employee_id == employee_id,
                TimeSlot.is_booked.is_(False),
                TimeSlot.slot_at >= date_from,
                TimeSlot.slot_at < date_to,
            )
            .order_by(TimeSlot.slot_at)
        )
        slots = list((await self.session.execute(stmt)).scalars().all())

        windows: list[dict] = []
        i = 0
        while i <= len(slots) - slots_needed:
            run = [slots[i]]
            for j in range(1, slots_needed):
                expected = slots[i].slot_at + timedelta(minutes=SLOT_MIN * j)
                if slots[i + j].slot_at == expected:
                    run.append(slots[i + j])
                else:
                    break
            if len(run) == slots_needed:
                windows.append({
                    "start_at": run[0].slot_at,
                    "end_at": run[-1].slot_at + timedelta(minutes=SLOT_MIN),
                    "slot_ids": [s.id for s in run],
                })
                if len(windows) >= max_results:
                    break
                i += slots_needed
            else:
                i += 1
        return windows

    async def validate_slots(
        self,
        slot_ids: list[int],
        employee_id: str,
        start_at: datetime,
        end_at: datetime,
    ) -> dict:
        """Перевіряє що slot_ids валідні: існують, належать майстру, не зайняті,
        і покривають [start_at, end_at).

        Повертає {"ok": True} або {"ok": False, "error": "<message>"}.

        Це security boundary: захищає від агента що пропустив get_available_slots
        і галюцинує slot_ids.
        """
        if not slot_ids:
            return {"ok": False, "error": "slot_ids is empty — call get_available_slots first to get valid slot ids"}

        slots = list((
            await self.session.execute(
                select(TimeSlot).where(TimeSlot.id.in_(slot_ids))
            )
        ).scalars().all())

        # Усі ID існують?
        found_ids = {s.id for s in slots}
        missing = [sid for sid in slot_ids if sid not in found_ids]
        if missing:
            return {
                "ok": False,
                "error": f"slot_ids do not exist: {missing[:5]} — do not invent slot ids, use get_available_slots",
            }

        # Усі належать одному майстру?
        wrong_master = [s.id for s in slots if s.employee_id != employee_id]
        if wrong_master:
            return {
                "ok": False,
                "error": f"slot_ids belong to a different master — call get_available_slots with master_id={employee_id}",
            }

        # Жоден не зайнятий?
        booked = [s.id for s in slots if s.is_booked]
        if booked:
            return {
                "ok": False,
                "error": f"slot_ids already booked: {booked[:5]} — call get_available_slots to refresh",
            }

        # Час слотів покриває [start_at, end_at)?
        slots_sorted = sorted(slots, key=lambda s: s.slot_at)
        actual_start = slots_sorted[0].slot_at
        actual_end = slots_sorted[-1].slot_at + timedelta(minutes=SLOT_MIN)
        # Допускаємо timezone несумісність — порівнюємо naive
        a_start = actual_start.replace(tzinfo=None) if actual_start.tzinfo else actual_start
        a_end = actual_end.replace(tzinfo=None) if actual_end.tzinfo else actual_end
        s_start = start_at.replace(tzinfo=None) if start_at.tzinfo else start_at
        s_end = end_at.replace(tzinfo=None) if end_at.tzinfo else end_at
        if a_start != s_start or a_end != s_end:
            return {
                "ok": False,
                "error": (
                    f"slot times mismatch: slots cover [{a_start.isoformat()}, {a_end.isoformat()}) "
                    f"but you passed start_at={s_start.isoformat()} end_at={s_end.isoformat()}. "
                    f"Use exactly the values from get_available_slots."
                ),
            }

        return {"ok": True}

    async def mark_booked(self, slot_ids: list[int], booking_id: int) -> None:
        await self.session.execute(
            update(TimeSlot)
            .where(TimeSlot.id.in_(slot_ids))
            .values(is_booked=True, booking_id=booking_id)
        )

    async def release(self, booking_id: int) -> None:
        await self.session.execute(
            update(TimeSlot)
            .where(TimeSlot.booking_id == booking_id)
            .values(is_booked=False, booking_id=None)
        )
