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

    # Whisper supported: mp3, mp4, mpeg, mpga, m4a, wav, webm, ogg, flac
    _SAFE_EXT = {"mp3", "mp4", "mpeg", "mpga", "m4a", "wav", "webm", "ogg", "flac", "oga"}
    _MIME_TO_EXT = {
        "audio/webm": "webm",
        "audio/ogg": "ogg",
        "audio/mp4": "m4a",
        "audio/m4a": "m4a",
        "audio/x-m4a": "m4a",
        "audio/mpeg": "mp3",
        "audio/mp3": "mp3",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/flac": "flac",
        "audio/aac": "m4a",
    }

    async def transcribe(self, audio_bytes: bytes, mime: str, language: str | None = None) -> str:
        # Sanitize mime → safe extension (Whisper rejects unknown ones з codecs suffix).
        mime_clean = (mime or "audio/webm").split(";")[0].strip().lower()
        ext = self._MIME_TO_EXT.get(mime_clean)
        if not ext:
            tail = mime_clean.split("/")[-1] if "/" in mime_clean else mime_clean
            ext = tail if tail in self._SAFE_EXT else "webm"
        buf = io.BytesIO(audio_bytes)
        buf.name = f"audio.{ext}"
        result = await self._client.audio.transcriptions.create(
            model=self._model,
            file=buf,
            language=language,
        )
        return result.text
