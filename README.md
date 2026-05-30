# AI Sales Manager — мережа салонів краси

AI менеджер з продажу для мережі салонів краси. Підключається до месенджерів
(Telegram, Instagram, Facebook Messenger, Viber, WhatsApp), приймає текст / фото / аудіо,
відповідає текстом. По одному агенту на країну з власною БД салонів, послуг, майстрів та слотів.

## Стек
- **FastAPI** — webhooks
- **Anthropic Claude** (`claude-sonnet-4-6`) — основна LLM з tool use
- **OpenAI Whisper** — транскрипція голосових
- **PostgreSQL + SQLAlchemy 2.0 (async)** — основна БД
- **Redis** — кеш діалогів
- **Arq** — фонові задачі

## Архітектура
Hexagonal (Ports & Adapters) + DDD. Дивись [app/](app/) — кожен шар має чітку відповідальність:

- [app/api/](app/api/) — HTTP webhooks
- [app/domain/](app/domain/) — pure business logic (моделі, сервіси)
- [app/adapters/](app/adapters/) — інтеграції (messengers, llm, transcription)
- [app/agents/](app/agents/) — sales agent + інструкції + tools
- [app/infrastructure/](app/infrastructure/) — БД, Redis, черги

## Запуск (dev)
```bash
cp .env.example .env  # заповни ключі
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

## Наступні кроки
- [ ] Схема БД (Salon, Service, Master, TimeSlot, Booking, Client) + Alembic міграції
- [ ] Реалізація tools: search_services, get_available_slots, create_booking
- [ ] TelegramAdapter (parse_webhook, download_media, send_text)
- [ ] Інші месенджери (WhatsApp Cloud API, Instagram/FB Graph, Viber)
- [ ] DI у FastAPI (lifespan + Depends)
- [ ] Workers (Arq) для довгих задач
- [ ] Тести (unit + integration)
