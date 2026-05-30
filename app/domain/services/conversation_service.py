"""Оркестрація обробки вхідного повідомлення: від IncomingMessage до відправленої відповіді."""
import base64
import logging
from typing import Any

from app.adapters.messengers.base import MessengerAdapter
from app.adapters.transcription.base import TranscriptionService
from app.agents.agent_factory import AgentFactory
from app.domain.models.conversation import Conversation, ConversationTurn
from app.domain.models.message import IncomingMessage, MessageType, OutgoingMessage
from app.infrastructure.redis.conversation_cache import ConversationCache

log = logging.getLogger(__name__)


class ConversationService:
    def __init__(
        self,
        agent_factory: AgentFactory,
        cache: ConversationCache,
        transcription: TranscriptionService,
        messenger_registry: dict[str, MessengerAdapter],
    ) -> None:
        self._agents = agent_factory
        self._cache = cache
        self._transcription = transcription
        self._messengers = messenger_registry

    async def handle_batch(
        self, messages: list[IncomingMessage], credentials: dict[str, Any]
    ) -> None:
        """Обробляє батч повідомлень одного клієнта як ОДИН user turn.

        Викликається з MessageBatcher після debounce-вікна. Усі повідомлення
        в батчі належать одному діалогу (один external_chat_id).
        """
        if not messages:
            return

        first = messages[0]
        messenger = self._messengers[first.channel.value]
        conversation = await self._load_conversation(first)

        # Будуємо ОДИН user content з усіх повідомлень батчу:
        # - текстові злипаються в один блок з переходами
        # - кожне фото — окремий image блок (Claude/GPT vision)
        # - аудіо транскрибуються і додаються як текст
        merged_content: list[dict[str, Any]] = []
        text_pieces: list[str] = []
        has_image = False

        for msg in messages:
            content = await self._build_user_content(msg, messenger, credentials)
            for block in content:
                if block.get("type") == "text":
                    txt = block.get("text", "").strip()
                    if txt:
                        text_pieces.append(txt)
                elif block.get("type") in ("image", "image_url"):
                    has_image = True
                    merged_content.append(block)

        if text_pieces:
            merged_content.insert(0, {"type": "text", "text": "\n".join(text_pieces)})

        if not merged_content:
            return

        text_for_history = "\n".join(text_pieces) or "[media]"

        log.info(
            "[BATCH] %s/%s chat=%s msgs=%d history_len=%d salon=%s | USER: %r",
            first.channel.value,
            first.country,
            first.external_chat_id,
            len(messages),
            len(conversation.history),
            conversation.salon_id,
            text_for_history[:200],
        )

        agent = self._agents.get_agent(first.country)
        reply_text = await agent.respond(conversation, merged_content)
        log.info(
            "[REPLY] chat=%s | ASSISTANT: %r",
            first.external_chat_id,
            reply_text[:300],
        )

        conversation.history.append(
            ConversationTurn(role="user", content=text_for_history, has_image=has_image)
        )
        conversation.history.append(ConversationTurn(role="assistant", content=reply_text))
        await self._cache.save(conversation)

        await messenger.send_text(
            OutgoingMessage(
                channel=first.channel,
                external_chat_id=first.external_chat_id,
                text=reply_text,
            ),
            credentials=credentials,
        )

    async def process_web_turn(
        self,
        messages: list[IncomingMessage],
        on_tool_event=None,
    ) -> str:
        """Web flow: синхронна обробка без messenger.send.
        Будує content з messages (text/audio вже транскрибовані на рівні роута,
        image приходить як base64 в media_url=data:base64,...), викликає агента,
        повертає reply text. Зберігає історію в кеш як завжди.
        """
        if not messages:
            return ""

        first = messages[0]
        conversation = await self._load_conversation(first)

        merged_content: list[dict[str, Any]] = []
        text_pieces: list[str] = []
        has_image = False

        for msg in messages:
            if msg.message_type == MessageType.IMAGE and msg.media_url and msg.media_url.startswith("data:"):
                # web flow: image вже в base64
                try:
                    header, b64data = msg.media_url.split(",", 1)
                    media_type = header.split(";")[0].split(":")[1] if ":" in header else "image/jpeg"
                except (ValueError, IndexError):
                    log.warning("invalid data URL")
                    continue
                merged_content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": b64data},
                })
                has_image = True
                if msg.text:
                    text_pieces.append(msg.text)
            elif msg.text:
                text_pieces.append(msg.text)

        if text_pieces:
            merged_content.insert(0, {"type": "text", "text": "\n".join(text_pieces)})

        if not merged_content:
            return ""

        text_for_history = "\n".join(text_pieces) or "[media]"

        log.info(
            "[WEB] %s/%s chat=%s msgs=%d history_len=%d salon=%s | USER: %r",
            first.channel.value, first.country, first.external_chat_id,
            len(messages), len(conversation.history), conversation.salon_id,
            text_for_history[:200],
        )

        agent = self._agents.get_agent(first.country)
        reply_text = await agent.respond(conversation, merged_content, on_tool_event=on_tool_event)
        log.info("[WEB REPLY] chat=%s | ASSISTANT: %r", first.external_chat_id, reply_text[:300])

        conversation.history.append(
            ConversationTurn(role="user", content=text_for_history, has_image=has_image)
        )
        conversation.history.append(ConversationTurn(role="assistant", content=reply_text))
        await self._cache.save(conversation)
        return reply_text

    async def _load_conversation(self, msg: IncomingMessage) -> Conversation:
        # Один TG-аккаунт обслуговує всі салони країни. Один клієнт = одна розмова
        # на рівні (channel, country, chat). Salon обирається в межах діалогу.
        conv_id = f"{msg.channel.value}:{msg.country}:{msg.external_chat_id}"
        conv = await self._cache.load(conv_id)
        if conv is None:
            conv = Conversation(
                conversation_id=conv_id,
                country=msg.country,
                channel=msg.channel.value,
                salon_id=msg.salon_id,  # None для country-level
            )
        return conv

    async def _build_user_content(
        self,
        msg: IncomingMessage,
        messenger: MessengerAdapter,
        credentials: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Готує content blocks для Claude (text + опц. image)."""
        blocks: list[dict[str, Any]] = []

        if msg.message_type == MessageType.AUDIO and msg.media_url:
            audio_bytes = await messenger.download_media(msg.media_url, credentials)
            text = await self._transcription.transcribe(audio_bytes, msg.media_mime or "audio/ogg")
            blocks.append({"type": "text", "text": text})

        elif msg.message_type == MessageType.IMAGE and msg.media_url:
            img_bytes = await messenger.download_media(msg.media_url, credentials)
            b64 = base64.standard_b64encode(img_bytes).decode("ascii")
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": msg.media_mime or "image/jpeg",
                    "data": b64,
                },
            })
            if msg.text:
                blocks.append({"type": "text", "text": msg.text})

        else:
            blocks.append({"type": "text", "text": msg.text or ""})

        return blocks

    def _extract_text_for_history(self, content: list[dict[str, Any]]) -> str:
        return " ".join(b.get("text", "") for b in content if b.get("type") == "text").strip() or "[media]"
