"""Створює admin user в БД.

Запуск:
    python -m scripts.seed_admin --email you@example.com --password StrongPass123
    python -m scripts.seed_admin --email admin@local --password admin123 --display-name "Main Admin"
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.admin.auth import hash_password
from app.infrastructure.db.models.eval import AdminUser
from app.infrastructure.db.repositories.eval_repo import AdminUserRepository
from app.infrastructure.db.session import build_engine, build_session_factory


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--display-name", default=None)
    parser.add_argument("--update", action="store_true",
                        help="Якщо користувач існує — оновити пароль")
    args = parser.parse_args()

    if len(args.password) < 6:
        print("ERROR: password must be at least 6 characters", file=sys.stderr)
        sys.exit(1)

    engine = build_engine()
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            repo = AdminUserRepository(session)
            existing = await repo.get_by_email(args.email)
            if existing:
                if not args.update:
                    print(f"ERROR: user {args.email} already exists. Use --update to change password.",
                          file=sys.stderr)
                    sys.exit(1)
                existing.password_hash = hash_password(args.password)
                if args.display_name:
                    existing.display_name = args.display_name
                await session.commit()
                print(f"OK: updated password for {args.email}")
            else:
                user = await repo.create(
                    email=args.email,
                    password_hash=hash_password(args.password),
                    display_name=args.display_name,
                )
                await session.commit()
                print(f"OK: created admin user {user.email} (id={user.id})")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())
