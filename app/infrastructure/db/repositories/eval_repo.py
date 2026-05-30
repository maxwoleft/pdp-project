"""Repository для EvalScenario і AdminUser. Все в схемі public."""
from datetime import datetime
from typing import Sequence

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.eval import AdminUser, EvalScenario


class EvalScenarioRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(
        self,
        country: str | None = None,
        tag: str | None = None,
        search: str | None = None,
        enabled_only: bool = False,
    ) -> list[EvalScenario]:
        stmt = select(EvalScenario).order_by(EvalScenario.slug)
        if country:
            stmt = stmt.where(EvalScenario.country == country)
        if enabled_only:
            stmt = stmt.where(EvalScenario.enabled.is_(True))
        if search:
            term = f"%{search}%"
            stmt = stmt.where(or_(
                EvalScenario.slug.ilike(term),
                EvalScenario.description.ilike(term),
            ))
        rows = (await self.session.execute(stmt)).scalars().all()
        if tag:
            rows = [r for r in rows if tag in (r.tags or [])]
        return list(rows)

    async def get_by_id(self, scenario_id: str) -> EvalScenario | None:
        stmt = select(EvalScenario).where(EvalScenario.id == scenario_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> EvalScenario | None:
        stmt = select(EvalScenario).where(EvalScenario.slug == slug)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create(self, **kwargs) -> EvalScenario:
        scenario = EvalScenario(**kwargs)
        self.session.add(scenario)
        await self.session.flush()
        return scenario

    async def update(self, scenario_id: str, **kwargs) -> EvalScenario | None:
        scenario = await self.get_by_id(scenario_id)
        if not scenario:
            return None
        for key, value in kwargs.items():
            if hasattr(scenario, key):
                setattr(scenario, key, value)
        scenario.updated_at = datetime.utcnow()
        await self.session.flush()
        return scenario

    async def delete(self, scenario_id: str) -> bool:
        scenario = await self.get_by_id(scenario_id)
        if not scenario:
            return False
        await self.session.delete(scenario)
        await self.session.flush()
        return True

    async def all_tags(self) -> list[str]:
        rows = (await self.session.execute(select(EvalScenario.tags))).scalars().all()
        tags: set[str] = set()
        for r in rows:
            if r:
                tags.update(r)
        return sorted(tags)

    async def stats(self) -> dict:
        rows = (await self.session.execute(select(EvalScenario))).scalars().all()
        by_country: dict[str, int] = {}
        for r in rows:
            by_country[r.country] = by_country.get(r.country, 0) + 1
        return {
            "total": len(rows),
            "enabled": sum(1 for r in rows if r.enabled),
            "by_country": by_country,
        }


class AdminUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_email(self, email: str) -> AdminUser | None:
        stmt = select(AdminUser).where(AdminUser.email == email.lower().strip())
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_id(self, user_id: str) -> AdminUser | None:
        stmt = select(AdminUser).where(AdminUser.id == user_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create(self, *, email: str, password_hash: str, display_name: str | None = None) -> AdminUser:
        user = AdminUser(
            email=email.lower().strip(),
            password_hash=password_hash,
            display_name=display_name,
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def touch_login(self, user_id: str) -> None:
        await self.session.execute(
            update(AdminUser).where(AdminUser.id == user_id).values(last_login_at=datetime.utcnow())
        )
