"""Repository для public.country_messenger."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.common import CountryMessenger


class CountryMessengerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, messenger_id: str) -> CountryMessenger | None:
        return await self.session.get(CountryMessenger, messenger_id)

    async def find_by_account(
        self, channel: str, external_account_id: str
    ) -> CountryMessenger | None:
        stmt = select(CountryMessenger).where(
            CountryMessenger.channel == channel,
            CountryMessenger.external_account_id == external_account_id,
            CountryMessenger.is_active.is_(True),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_country(self, country_code: str) -> list[CountryMessenger]:
        stmt = select(CountryMessenger).where(CountryMessenger.country_code == country_code)
        return list((await self.session.execute(stmt)).scalars().all())

    async def create(self, **kwargs) -> CountryMessenger:
        cm = CountryMessenger(**kwargs)
        self.session.add(cm)
        await self.session.flush()
        return cm
