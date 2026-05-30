"""FastAPI dependencies для admin panel."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.auth import SESSION_COOKIE, decode_session_token
from app.infrastructure.db.models.eval import AdminUser
from app.infrastructure.db.repositories.eval_repo import (
    AdminUserRepository,
    EvalScenarioRepository,
)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Видаляє public-сесію (без country search_path)."""
    factory = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def current_admin_user_optional(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AdminUser | None:
    """Повертає AdminUser або None — для роутів які працюють і без авторизації."""
    token = request.cookies.get(SESSION_COOKIE)
    payload = decode_session_token(token)
    if not payload:
        return None
    repo = AdminUserRepository(session)
    user = await repo.get_by_id(payload.get("uid", ""))
    if not user or not user.is_active:
        return None
    return user


async def current_admin_user(
    request: Request,
    user: AdminUser | None = Depends(current_admin_user_optional),
) -> AdminUser:
    """Жорсткий guard — кидає 401 для API або редірект для HTML."""
    if not user:
        # Якщо це HTMX-запит — повертаємо HX-Redirect
        if request.headers.get("HX-Request"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="not authenticated",
                headers={"HX-Redirect": "/admin/login"},
            )
        # Браузерний запит — редірект
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="not authenticated",
            headers={"Location": "/admin/login"},
        )
    return user


def get_scenario_repo(
    session: AsyncSession = Depends(get_session),
) -> EvalScenarioRepository:
    return EvalScenarioRepository(session)


def get_user_repo(
    session: AsyncSession = Depends(get_session),
) -> AdminUserRepository:
    return AdminUserRepository(session)


def get_category_group_repo(session: AsyncSession = Depends(get_session)):
    from app.infrastructure.db.repositories.category_group_repo import (
        CategoryGroupRepository,
    )
    return CategoryGroupRepository(session)


def get_profile_repo(session: AsyncSession = Depends(get_session)):
    from app.infrastructure.db.repositories.profile_repo import (
        ServiceProfileRepository,
    )
    return ServiceProfileRepository(session)
