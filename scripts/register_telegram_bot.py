"""Реєструє Telegram бот для конкретної КРАЇНИ + (опційно) встановлює webhook.

Один бот = одна країна = всі салони цієї країни. Salon обирається в розмові
через list_salons (агент пропонує клієнту).

Запуск:
  python -m scripts.register_telegram_bot \\
      --country gb \\
      --bot-token 123456:ABC... \\
      [--display-name "UK Bot"] \\
      [--public-url https://your-domain.com] \\
      [--no-set-webhook]
"""
from __future__ import annotations

import argparse
import asyncio
import secrets

from app.adapters.messengers.telegram import TelegramAdapter
from app.infrastructure.db.repositories.messenger_repo import CountryMessengerRepository
from app.infrastructure.db.session import build_engine, build_session_factory


async def amain() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--country", required=True, choices=["ua", "pl", "gb"])
    p.add_argument("--bot-token", required=True)
    p.add_argument("--display-name", default=None)
    p.add_argument("--public-url", default=None,
                   help="Базовий публічний URL застосунку. Якщо є — викличе TG setWebhook.")
    p.add_argument("--no-set-webhook", action="store_true")
    args = p.parse_args()

    tg = TelegramAdapter()
    try:
        # 1. Дізнаємось bot_id (унікальний external_account_id)
        me = await tg.get_me(args.bot_token)
        bot_id = str(me.get("id") or "")
        bot_username = me.get("username") or ""
        if not bot_id:
            raise SystemExit("getMe failed — invalid bot token?")

        engine = build_engine()
        factory = build_session_factory(engine)
        webhook_secret = secrets.token_urlsafe(32)

        async with factory() as session:
            repo = CountryMessengerRepository(session)
            existing = await repo.find_by_account("telegram", bot_id)
            if existing:
                existing.country_code = args.country
                existing.credentials = {"bot_token": args.bot_token, "bot_username": bot_username}
                existing.display_name = args.display_name or f"{args.country.upper()} Bot"
                existing.is_active = True
                if not existing.webhook_secret:
                    existing.webhook_secret = webhook_secret
                messenger_id = existing.id
                effective_secret = existing.webhook_secret
                action = "updated"
            else:
                cm = await repo.create(
                    country_code=args.country,
                    channel="telegram",
                    external_account_id=bot_id,
                    display_name=args.display_name or f"{args.country.upper()} Bot",
                    credentials={"bot_token": args.bot_token, "bot_username": bot_username},
                    webhook_secret=webhook_secret,
                    is_active=True,
                )
                messenger_id = cm.id
                effective_secret = webhook_secret
                action = "created"
            await session.commit()

        print(f"\n✓ {action} country_messenger:")
        print(f"  id           = {messenger_id}")
        print(f"  country      = {args.country}")
        print(f"  bot_username = @{bot_username}")
        print(f"  bot_id       = {bot_id}")

        if args.public_url and not args.no_set_webhook:
            url = f"{args.public_url.rstrip('/')}/api/v1/webhooks/telegram/{messenger_id}"
            result = await tg.set_webhook(args.bot_token, url, secret_token=effective_secret)
            print(f"\n✓ Telegram setWebhook → {url}")
            print(f"  result: {result}")
        else:
            print("\nℹ Webhook NOT set automatically. Manual command:")
            print(f"  curl 'https://api.telegram.org/bot{args.bot_token}/setWebhook' \\")
            print(f"       -d 'url=YOUR_PUBLIC_URL/api/v1/webhooks/telegram/{messenger_id}' \\")
            print(f"       -d 'secret_token={effective_secret}'")

        await engine.dispose()
    finally:
        await tg.close()


if __name__ == "__main__":
    asyncio.run(amain())
