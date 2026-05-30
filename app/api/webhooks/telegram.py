"""Telegram webhook.

Стратегія: відповідаємо 200 МИТТЄВО, повідомлення додаємо в batcher.
Реальна обробка йде у фоні після debounce-вікна. Це гарантує, що Telegram
ніколи не отримає 5xx і не буде ретраїти.
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.adapters.messengers.base import MessengerAdapter
from app.core.config import get_settings
from app.core.dependencies import get_message_batcher, get_messenger
from app.domain.services.message_batcher import MessageBatcher
from app.infrastructure.db.repositories.messenger_repo import CountryMessengerRepository

router = APIRouter()


@router.post("/{messenger_id}")
async def telegram_webhook(
    messenger_id: str,
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
    batcher: MessageBatcher = Depends(get_message_batcher),
    telegram: MessengerAdapter = Depends(get_messenger("telegram")),
) -> dict:
    # 1. Резолвимо messenger → country + credentials
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        repo = CountryMessengerRepository(session)
        cm = await repo.get_by_id(messenger_id)
        if not cm or not cm.is_active or cm.channel != "telegram":
            raise HTTPException(status_code=404, detail="Unknown messenger")

        if cm.webhook_secret and x_telegram_bot_api_secret_token != cm.webhook_secret:
            raise HTTPException(status_code=401, detail="Invalid secret token")

        country = cm.country_code
        credentials = dict(cm.credentials)

    # Єдине джерело правди для bot_token — .env (TELEGRAM_BOT_TOKEN).
    # Поточна архітектура мережі — один бот на проект; БД-credentials
    # лишаються лише як fallback для legacy-записів.
    settings = get_settings()
    if settings.telegram_bot_token:
        credentials["bot_token"] = settings.telegram_bot_token

    payload = await request.json()

    try:
        messages = await telegram.parse_webhook(
            payload, country, salon_id=None, credentials=credentials
        )
        for msg in messages:
            await batcher.add(msg, credentials)
    except Exception:  # noqa: BLE001
        # Логування є на рівні uvicorn — не повертаємо 5xx, щоб Telegram не ретраїв
        import logging
        logging.getLogger(__name__).exception("Webhook processing error")

    return {"ok": True}
