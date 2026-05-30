"""Транскрипція аудіо через OpenAI Whisper."""
import io

from openai import AsyncOpenAI

from app.adapters.transcription.base import TranscriptionService
from app.core.config import get_settings


class OpenAIWhisper(TranscriptionService):
    def __init__(self) -> None:
        settings = get_settings()
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_whisper_model

    async def transcribe(self, audio_bytes: bytes, mime: str, language: str | None = None) -> str:
        ext = mime.split("/")[-1] if mime else "ogg"
        buf = io.BytesIO(audio_bytes)
        buf.name = f"audio.{ext}"
        result = await self._client.audio.transcriptions.create(
            model=self._model,
            file=buf,
            language=language,
        )
        return result.text
