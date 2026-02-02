import logging
from typing import Any, Dict, List

from app.domain.message import MediaContent, TextContent
from app.domain.ports import MessengerAdapter
from app.domain.webhooks.chatwoot import ChatwootMessageCreatedWebhook

logger = logging.getLogger(__name__)

# Extensões de áudio aceites para enviar como voice/audio no Telegram
AUDIO_EXTENSIONS = {"ogg", "oga", "m4a", "mp3", "opus", "wav"}
AUDIO_FILE_TYPES = {"audio", "voice"}


def _dig(src: dict, *path, default=None):
    """Safe dict traversal: _dig(d, 'a','b','c') -> d['a']['b']['c'] or default."""
    cur: Any = src
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


class MessageRouter:
    """Router: dispatch outgoing text messages to channel adapters."""

    def __init__(
        self,
        adapters: Dict[str, MessengerAdapter] | None = None,
        chatwoot_base_url: str | None = None,
    ):
        self.adapters = adapters or {}
        # Para resolver URLs relativas dos anexos (ex.: /rails/active_storage/...)
        self._chatwoot_base = (chatwoot_base_url or "").rstrip("/")

    async def handle_incoming(self, msg):
        # Not implemented in this demo
        logger.info(
            "[router] INCOMING: channel=%s recipient_id=%s sender_name=%s content=%s",
            getattr(msg, "channel", None),
            getattr(msg, "recipient_id", None),
            getattr(msg, "sender_name", None),
            getattr(msg, "content", None),
        )

    def _derive_recipient_id(self, channel: str | None, payload: dict) -> str | None:
        """
        Build recipient_id per channel. We never read it from Chatwoot.
        whatsapp:
          - conversation.meta.sender.phone_number
        telegram:
          1) sender.custom_attributes.telegram_username                  -> '@username' or 'username'
          2) sender.additional_attributes.social_telegram_user_name      -> '@username'
          3) sender.phone_number                                         -> '+7999...'
          4) sender.custom_attributes.telegram_user_id                   -> 'id:<int>'
          5) sender.additional_attributes.social_telegram_user_id        -> 'id:<int>'
        vk:
          1) sender.custom_attributes.vk_peer_id                         -> '<int>'
          2) sender.custom_attributes.vk_user_id                         -> '<int>'
        """
        if not channel:
            return None

        sender = _dig(payload, "conversation", "meta", "sender", default={}) or {}

        if channel == "whatsapp":
            phone = (sender.get("phone_number") or "").strip()
            return phone or None

        if channel == "telegram":
            # 1) username from custom attributes
            username = (sender.get("custom_attributes", {}) or {}).get(
                "telegram_username", ""
            )
            username = (username or "").strip()
            if username:
                return username

            # 2) username from additional attributes (added by Chatwoot TG bot)
            social_username = (sender.get("additional_attributes", {}) or {}).get(
                "social_telegram_user_name", ""
            )
            social_username = (social_username or "").strip()
            if social_username:
                return social_username

            # 3) phone number
            phone = (sender.get("phone_number") or "").strip()
            if phone:
                return phone

            # 4) numeric user id from custom attributes
            tg_uid = (sender.get("custom_attributes", {}) or {}).get("telegram_user_id")
            if tg_uid is not None and str(tg_uid).strip():
                return f"id:{tg_uid}"

            # 5) numeric user id from additional attributes (added by Chatwoot TG bot)
            social_tg_uid = (sender.get("additional_attributes", {}) or {}).get(
                "social_telegram_user_id"
            )
            if social_tg_uid is not None and str(social_tg_uid).strip():
                return f"id:{social_tg_uid}"

            return None

        if channel == "vk":
            # 1) peer_id from custom attributes
            vk_peer_id = (sender.get("custom_attributes", {}) or {}).get("vk_peer_id")
            if vk_peer_id is not None and str(vk_peer_id).strip():
                return str(vk_peer_id).strip()

            # 2) user_id from custom attributes
            vk_user_id = (sender.get("custom_attributes", {}) or {}).get("vk_user_id")
            if vk_user_id is not None and str(vk_user_id).strip():
                return str(vk_user_id).strip()

            return None

        # Other channels: do not guess
        return None

    def _resolve_attachment_url(self, data_url: str) -> str:
        """Converte URL relativa do Chatwoot em absoluta (necessário para download)."""
        url = (data_url or "").strip()
        if url.startswith("/") and self._chatwoot_base:
            return f"{self._chatwoot_base}{url}"
        return url

    def _first_audio_attachment(
        self, attachments: List[Dict[str, Any]]
    ) -> MediaContent | None:
        """Extrai o primeiro anexo de áudio (data_url) para enviar ao Telegram."""
        for att in attachments:
            if not isinstance(att, dict):
                continue
            file_type = (att.get("file_type") or "").lower()
            ext = (att.get("extension") or "").lstrip(".").lower()
            data_url = (att.get("data_url") or att.get("file_url") or "").strip()
            if not data_url:
                continue
            if file_type in AUDIO_FILE_TYPES or ext in AUDIO_EXTENSIONS:
                url = self._resolve_attachment_url(data_url)
                return MediaContent(
                    type="media",
                    media_type="audio",
                    url=url,
                    caption=None,
                    filename=att.get("filename"),
                    mime_type=att.get("content_type"),
                )
        return None

    async def handle_outgoing(self, payload: dict) -> None:
        """
        Process Chatwoot outgoing webhook and dispatch text to a proper adapter.
        Note: we trust channel injected at HTTP layer: payload['conversation']['meta']['channel'].
        """
        try:
            cw = ChatwootMessageCreatedWebhook.model_validate(payload)
        except Exception as e:
            logger.warning("[router] Invalid Chatwoot payload: %s", e)
            return

        if cw.event != "message_created":
            logger.info("[router] Ignored Chatwoot event: %s", cw.event)
            return
        if cw.private:
            logger.info("[router] Ignored private message")
            return
        if cw.message_type != "outgoing":
            logger.info("[router] Ignored message_type: %s", cw.message_type)
            return

        # Channel comes from raw payload (HTTP layer injected it into meta)
        channel = _dig(payload, "conversation", "meta", "channel")
        text = (cw.content or "").strip()

        # Always derive recipient_id (Chatwoot never provides it)
        recipient_id = self._derive_recipient_id(channel=channel, payload=payload)

        if not channel or not recipient_id:
            logger.warning(
                "[router] Missing fields: channel=%r recipient_id=%r",
                channel,
                recipient_id,
            )
            return

        # Anexos: payload pode ter "attachments" no topo, em content_attributes ou em message
        attachments: List[Dict[str, Any]] = payload.get("attachments") or []
        if not attachments and isinstance(_dig(payload, "content_attributes"), dict):
            attachments = _dig(payload, "content_attributes", "attachments") or []
        if not attachments:
            attachments = _dig(payload, "message", "attachments") or []

        # Texto não é obrigatório: pode enviar só áudio (ou outro anexo). Exige texto OU anexo.
        if not text and not attachments:
            logger.warning(
                "[router] Missing content: channel=%r recipient_id=%r text=%r attachments=%s",
                channel,
                recipient_id,
                text,
                len(attachments),
            )
            return

        if text:
            await self.dispatch_outbound(
                channel=channel, recipient_id=recipient_id, text=text
            )

        # Primeiro anexo de áudio: enviar como media (voice no Telegram), com ou sem texto
        if attachments and channel == "telegram":
            first_audio = self._first_audio_attachment(attachments)
            if first_audio:
                await self.dispatch_outbound_media(
                    channel=channel,
                    recipient_id=recipient_id,
                    media=first_audio,
                )
            elif not text:
                logger.warning(
                    "[router] No text and no audio attachment found: attachments=%s",
                    [a.get("file_type") for a in attachments],
                )

    async def dispatch_outbound(
        self, channel: str, recipient_id: str, text: str
    ) -> None:
        """Send text via selected channel adapter."""
        adapter = self.adapters.get(channel)
        if not adapter:
            logger.warning("[router] No adapter for channel=%s", channel)
            return

        await adapter.send_text(recipient_id, TextContent(type="text", text=text))
        logger.info(
            "[router] OUTBOUND: channel=%s recipient_id=%s text=%r",
            channel,
            recipient_id,
            text,
        )

    async def dispatch_outbound_media(
        self, channel: str, recipient_id: str, media: MediaContent
    ) -> None:
        """Send media (e.g. áudio) via selected channel adapter."""
        adapter = self.adapters.get(channel)
        if not adapter:
            logger.warning("[router] No adapter for channel=%s", channel)
            return

        await adapter.send_media(recipient_id, media)
        logger.info(
            "[router] OUTBOUND MEDIA: channel=%s recipient_id=%s media_type=%s",
            channel,
            recipient_id,
            media.media_type,
        )
