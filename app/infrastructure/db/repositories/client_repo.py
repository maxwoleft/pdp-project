"""Клієнти. Lookup по messenger external_id, апсерт з CRM."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.scheduling import Client


def make_client_id(salon_id: str, crm_id: str) -> str:
    return f"{salon_id}:{crm_id}"


class ClientRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, client_id: str) -> Client | None:
        return await self.session.get(Client, client_id)

    async def find_by_external(
        self, salon_id: str, channel: str, external_id: str
    ) -> Client | None:
        """Шукає клієнта в межах салону по ID з месенджера (Postgres JSONB ->>).

        Зверни увагу: external_ids — JSONB колонка, тому використовуємо
        ->> оператор через SQLAlchemy `astext`.
        """
        stmt = select(Client).where(
            Client.salon_id == salon_id,
            Client.external_ids[channel].astext == external_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def find_by_phone(self, salon_id: str, phone: str) -> Client | None:
        """Шукає клієнта по телефону. phone у нас JSONB array — використовуємо @> оператор."""
        stmt = select(Client).where(
            Client.salon_id == salon_id,
            Client.phone.contains([phone]),
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def link_external(
        self, client: Client, channel: str, external_id: str
    ) -> None:
        """Додає або оновлює маппінг месенджера для існуючого клієнта."""
        ext = dict(client.external_ids or {})
        ext[channel] = external_id
        client.external_ids = ext
        await self.session.flush()

    async def create_minimal(
        self,
        *,
        salon_id: str,
        crm_id: str,
        name: str | None = None,
        phone: str | None = None,
        channel: str | None = None,
        external_id: str | None = None,
    ) -> Client:
        """Створює мінімального клієнта (для нових, що ще не у CRM)."""
        client = Client(
            id=make_client_id(salon_id, crm_id),
            salon_id=salon_id,
            crm_id=crm_id,
            name=name,
            phone=[phone] if phone else None,
            external_ids={channel: external_id} if channel and external_id else {},
            status="Potential",
        )
        self.session.add(client)
        await self.session.flush()
        return client
