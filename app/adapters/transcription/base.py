from abc import ABC, abstractmethod


class TranscriptionService(ABC):
    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, mime: str, language: str | None = None) -> str:
        ...
