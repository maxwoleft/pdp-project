"""FastAPI entry point. lifespan створює всю інфраструктуру один раз."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

# Налаштовуємо root логер, щоб app.* логери писали в stdout (uvicorn їх ловить).
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logging.getLogger("app").setLevel(logging.INFO)

from app.adapters.embeddings.cached_embedder import CachedEmbedder
from app.adapters.embeddings.openai_embedder import OpenAIEmbedder
from app.adapters.llm.hybrid_router import HybridLLMRouter
from app.adapters.llm.query_rewriter import LLMReranker, QueryRewriter
from app.adapters.messengers.telegram import TelegramAdapter
from app.adapters.transcription.openai_whisper import OpenAIWhisper
from app.agents.agent_factory import AgentFactory
from app.api.router import api_router
from app.core.config import get_settings
from app.domain.services.conversation_service import ConversationService
from app.domain.services.message_batcher import MessageBatcher
from app.infrastructure.db.session import build_engine, build_session_factory
from app.infrastructure.redis.conversation_cache import ConversationCache


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # 1. Інфраструктура — пули створюємо ОДИН раз
    db_engine = build_engine()
    session_factory = build_session_factory(db_engine)
    redis = Redis.from_url(settings.redis_url)

    # 2. Адаптери
    llm = HybridLLMRouter()
    transcription = OpenAIWhisper()
    embedder = CachedEmbedder(OpenAIEmbedder(), redis)
    query_rewriter = QueryRewriter(redis=redis)
    # LLM reranker — opt-in через env: SEARCH_LLM_RERANK=1.
    # Дає +5-10% accuracy, але +300ms latency і ~$0.0005/запит.
    import os
    rerank_enabled = os.environ.get("SEARCH_LLM_RERANK", "").lower() in ("1", "true", "yes")
    llm_reranker = LLMReranker(redis=redis, enabled=rerank_enabled)
    cache = ConversationCache(redis)

    # 3. Месенджери — stateless, по одному інстансу на канал
    telegram_adapter = TelegramAdapter()
    messenger_registry: dict = {
        "telegram": telegram_adapter,
        # "whatsapp": ..., "instagram": ..., "facebook": ..., "viber": ...
    }

    # 4. AgentFactory: кешує по одному агенту на країну
    agent_factory = AgentFactory(
        session_factory=session_factory,
        llm=llm,
        embedder=embedder,
        query_rewriter=query_rewriter,
        llm_reranker=llm_reranker,
        redis=redis,
    )

    # 5. Головний оркестратор
    conversation_service = ConversationService(
        agent_factory=agent_factory,
        cache=cache,
        transcription=transcription,
        messenger_registry=messenger_registry,
    )

    # 6. Дебаунсер: склеює серії повідомлень одного клієнта в один turn
    message_batcher = MessageBatcher(handler=conversation_service.handle_batch)

    # 7. Експортуємо в app.state — звідти роути дістануть через Depends
    app.state.settings = settings
    app.state.db_engine = db_engine
    app.state.session_factory = session_factory
    app.state.redis = redis
    app.state.conversation_service = conversation_service
    app.state.message_batcher = message_batcher
    app.state.messenger_registry = messenger_registry

    yield

    # 8. Cleanup
    await message_batcher.shutdown()
    await telegram_adapter.close()
    await db_engine.dispose()
    await redis.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AI Sales Manager",
        version="0.1.0",
        debug=settings.app_env == "development",
        lifespan=lifespan,
    )
    app.include_router(api_router)

    # Admin panel
    from app.admin.routes import router as admin_router
    from app.admin.profile_routes import profile_router
    from app.admin.service_routes import service_router
    from app.admin.category_group_routes import router as category_tree_router
    app.include_router(admin_router)
    app.include_router(profile_router)
    app.include_router(service_router)
    app.include_router(category_tree_router)

    # Web chat (public)
    from app.api.chat.routes import router as chat_router
    app.include_router(chat_router)

    return app


app = create_app()
