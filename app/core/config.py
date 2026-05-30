"""Глобальний конфіг застосунку. Pydantic Settings читає з env."""
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    app_env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    anthropic_max_tokens: int = 2048

    # OpenAI (Whisper + GPT-5 mini для простих діалогів)
    openai_api_key: str = ""
    openai_whisper_model: str = "whisper-1"
    openai_chat_model: str = "gpt-5-mini"
    openai_chat_model_complex: str = "gpt-5"
    openai_max_tokens: int = 2048

    # DB / Redis
    database_url: str
    redis_url: str = "redis://localhost:6379/0"

    # Messenger tokens
    # Єдиний Telegram токен на весь проект — береться при відправці повідомлень
    # незалежно від країни. Якщо потрібна країнно-специфічна конфігурація —
    # використовуйте telegram_bot_token_{ua,pl} як override.
    telegram_bot_token: str | None = None
    telegram_bot_token_ua: str | None = None
    telegram_bot_token_pl: str | None = None
    whatsapp_token: str | None = None
    whatsapp_phone_id: str | None = None
    instagram_token: str | None = None
    facebook_page_token: str | None = None
    viber_token_ua: str | None = None

    telegram_webhook_secret: str | None = None
    meta_verify_token: str | None = None

    enabled_countries: str = "ua"

    # Admin panel: окремий секрет для підпису session cookies.
    # Має бути random, мінімум 32 байти. Згенерувати: `openssl rand -hex 32`
    admin_session_secret: str = ""

    # Web chat: окремий секрет для підпису cookie з chat_id/country.
    # Якщо не задано — fallback на admin_session_secret.
    web_chat_session_secret: str = ""

    # ── CRM push (api.aihelps.com) ────────────────────────────────
    # Якщо False — створення/скасування записів пишеться ТІЛЬКИ в нашу БД,
    # без виклику CRM. Використовується на час тестування, щоб не засмічувати
    # реальну систему салонів. Увімкнути перед продакшеном.
    crm_push_enabled: bool = False

    @property
    def countries(self) -> list[str]:
        return [c.strip().lower() for c in self.enabled_countries.split(",") if c.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
