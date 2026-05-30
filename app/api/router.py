from fastapi import APIRouter

from app.api.webhooks import telegram  # додамо інші пізніше

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(telegram.router, prefix="/webhooks/telegram", tags=["webhooks"])


@api_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
