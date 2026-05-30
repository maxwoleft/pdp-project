"""Telegram Bot API адаптер.

Stateless. credentials = {"bot_token": "...", "bot_username": "..."}
Один інстанс на весь app, обслуговує всі TG-аккаунти всіх салонів.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.adapters.messengers.base import MessengerAdapter
from app.domain.models.message import Channel, IncomingMessage, MessageType, OutgoingMessage

log = logging.getLogger(__name__)

TG_API = "https://api.telegram.org"
HTTP_TIMEOUT = 30.0


class TelegramAdapter(MessengerAdapter):
    channel_name = "telegram"

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=HTTP_TIMEOUT)

    async def close(self) -> None:
        await self._http.aclose()

    # ──────────────────────────────────────────────────────────────
    async def parse_webhook(
        self,
        payload: dict[str, Any],
        country: str,
        salon_id: str | None,
        credentials: dict[str, Any],
    ) -> list[IncomingMessage]:
        message = payload.get("message") or payload.get("edited_message")
        if not message:
            return []

        chat = message.get("chat") or {}
        from_user = message.get("from") or {}
        chat_id = str(chat.get("id"))
        user_id = str(from_user.get("id") or chat_id)

        common: dict[str, Any] = {
            "channel": Channel.TELEGRAM,
            "country": country,
            "salon_id": salon_id,
            "external_user_id": user_id,
            "external_chat_id": chat_id,
            "raw": payload,
        }

        # Voice / audio
        if "voice" in message or "audio" in message:
            file = message.get("voice") or message.get("audio")
            file_id = file.get("file_id")
            mime = file.get("mime_type") or "audio/ogg"
            return [
                IncomingMessage(
                    **common,
                    message_type=MessageType.AUDIO,
                    media_url=file_id,  # для TG це file_id, не URL — резолвимо в download_media
                    media_mime=mime,
                )
            ]

        # Image
        if "photo" in message:
            # photo — масив різних розмірів, беремо найбільшу
            photos = message["photo"]
            largest = max(photos, key=lambda p: p.get("file_size", 0))
            return [
                IncomingMessage(
                    **common,
                    message_type=MessageType.IMAGE,
                    media_url=largest["file_id"],
                    media_mime="image/jpeg",
                    text=message.get("caption"),
                )
            ]

        # Text
        text = message.get("text") or message.get("caption")
        if text:
            return [
                IncomingMessage(
                    **common,
                    message_type=MessageType.TEXT,
                    text=text,
                )
            ]

        return []

    # ──────────────────────────────────────────────────────────────
    async def download_media(self, media_url: str, credentials: dict[str, Any]) -> bytes:
        """media_url — це Telegram file_id. Резолвимо у реальний URL і качаємо."""
        token = credentials["bot_token"]
        info = await self._http.get(
            f"{TG_API}/bot{token}/getFile", params={"file_id": media_url}
        )
        info.raise_for_status()
        file_path = info.json()["result"]["file_path"]
        download = await self._http.get(f"{TG_API}/file/bot{token}/{file_path}")
        download.raise_for_status()
        return download.content

    # ──────────────────────────────────────────────────────────────
    async def send_text(self, message: OutgoingMessage, credentials: dict[str, Any]) -> None:
        token = credentials["bot_token"]
        # Telegram має ліміт 4096 символів на повідомлення — рідко, але буває
        text = message.text
        chunks = [text[i : i + 4000] for i in range(0, len(text), 4000)] or [""]
        for chunk in chunks:
            resp = await self._http.post(
                f"{TG_API}/bot{token}/sendMessage",
                json={"chat_id": message.external_chat_id, "text": chunk},
            )
            if resp.status_code >= 400:
                log.error("Telegram sendMessage failed: %s %s", resp.status_code, resp.text)
                resp.raise_for_status()

    # ──────────────────────────────────────────────────────────────
    async def set_webhook(
        self, bot_token: str, url: str, secret_token: str | None = None
    ) -> dict:
        """Реєстрація webhook URL у Telegram. Викликається з CLI при додаванні бота."""
        body: dict[str, Any] = {"url": url, "drop_pending_updates": True}
        if secret_token:
            body["secret_token"] = secret_token
        resp = await self._http.post(f"{TG_API}/bot{bot_token}/setWebhook", json=body)
        resp.raise_for_status()
        return resp.json()

    async def get_me(self, bot_token: str) -> dict:
        resp = await self._http.get(f"{TG_API}/bot{bot_token}/getMe")
        resp.raise_for_status()
        return resp.json().get("result", {})
