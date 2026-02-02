import asyncio
import base64
import logging
import os
import re
import tempfile
from typing import Optional

from pyee.asyncio import AsyncIOEventEmitter
from telethon import TelegramClient, errors, events, functions, types

import httpx

from app.config import TelegramConfig
from app.domain.message import MediaContent, TextContent
from app.domain.ports import MessengerAdapter, OnMessage

logger = logging.getLogger(__name__)

# Accept @username or plain username (min length 5)
USERNAME_RE = re.compile(r"^@?[A-Za-z0-9_]{5,}$")
# E.164-like phone pattern: optional + and 7..15 digits
PHONE_RE = re.compile(r"^\+?\d{7,15}$")


class TelegramAdapter(MessengerAdapter):
    """Telegram adapter (text only) using native Telethon client (non-bot)."""

    def __init__(self, bus: AsyncIOEventEmitter, config: TelegramConfig):
        self.bus = bus
        self._cfg = config
        self.inbox_id = config.inbox_id  # expose per-channel inbox
        self.client: Optional[TelegramClient] = None
        self._cb: Optional[OnMessage] = None

    def on_message(self, cb: OnMessage) -> None:
        self._cb = cb

    async def start(self) -> None:
        # 1) Ficheiro montado no Coolify (ex.: volume com session.session)
        session_path_env = os.getenv("TG_SESSION_PATH", "").strip()
        if session_path_env and os.path.isfile(session_path_env):
            session_path_or_name = session_path_env
            logger.info("[telegram] using session file from TG_SESSION_PATH=%s", session_path_env)
        else:
            # 2) Base64 na env (fallback)
            session_path = os.path.join(
                os.getcwd(), f"{self._cfg.session_name}.session"
            )
            session_b64 = os.getenv("TG_SESSION_BASE64", "").strip()
            if session_b64:
                try:
                    data = base64.b64decode(session_b64, validate=True)
                    with open(session_path, "wb") as f:
                        f.write(data)
                    logger.info(
                        "[telegram] session file written from TG_SESSION_BASE64"
                    )
                except Exception as e:
                    logger.warning(
                        "[telegram] failed to write session from TG_SESSION_BASE64: %s",
                        e,
                    )
            session_path_or_name = self._cfg.session_name

        self.client = TelegramClient(
            session_path_or_name,
            self._cfg.api_id,
            self._cfg.api_hash,
            device_model="iPhone 14",
            system_version="16.5",
            app_version="8.4.1",
            lang_code="en",
            system_lang_code="en-US",
        )
        await self.client.start()

        # Register handler for incoming messages (non-bot account)
        @self.client.on(events.NewMessage(incoming=True))
        async def handle_incoming(event):
            # Extract sender details
            sender = await event.get_sender()
            username = getattr(sender, "username", None)
            first_name = getattr(sender, "first_name", None)
            from_id = getattr(sender, "id", None)

            # Build message payload for internal bus
            payload = {
                "text": event.text or "",
                "from_id": str(from_id) if from_id else None,
                "username": username,
                "name": first_name or username or str(from_id),
            }
            # Áudio/voice: descarregar e anexar ao payload
            msg = getattr(event, "message", event)
            media = getattr(msg, "media", None)
            is_voice = bool(getattr(msg, "voice", False))
            is_audio = bool(getattr(msg, "audio", False))
            if media is not None and (is_voice or is_audio):
                try:
                    ext = ".ogg" if is_voice else ".m4a"
                    fd, path = tempfile.mkstemp(suffix=ext)
                    os.close(fd)
                    await self.client.download_media(msg, file=path)
                    payload["attachment_path"] = path
                    payload["attachment_content_type"] = (
                        "audio/ogg" if is_voice else "audio/mpeg"
                    )
                    if not payload["text"]:
                        payload["text"] = ""
                    logger.info(
                        "[telegram] INCOMING: from=%s áudio/voice -> Chatwoot",
                        from_id,
                    )
                except Exception as e:
                    logger.warning("[telegram] download media failed: %s", e)
            if "attachment_path" not in payload:
                logger.info(
                    "[telegram] INCOMING: from=%s (@%s) text=%r -> será enviado ao Chatwoot",
                    from_id,
                    username or "-",
                    (payload["text"] or "")[:80],
                )
            self.bus.emit("telegram.incoming", payload)

        # Mensagens enviadas por ti (disparos) -> enviar ao Chatwoot como outgoing
        @self.client.on(events.NewMessage(outgoing=True))
        async def handle_outgoing(event):
            try:
                peer = getattr(event, "peer_id", None) or getattr(
                    event.message, "peer_id", None
                )
                if not peer:
                    return
                # Só conversas privadas (user); grupos têm peer_id diferente
                if not isinstance(peer, types.PeerUser):
                    return
                recipient = await self.client.get_entity(peer)
                username = getattr(recipient, "username", None)
                first_name = getattr(recipient, "first_name", None)
                rid = getattr(recipient, "id", None)
                payload = {
                    "text": event.text or "",
                    "to_id": str(rid) if rid else None,
                    "username": username,
                    "name": first_name or username or (str(rid) if rid else "?"),
                }
                # Áudio/voice nos disparos
                msg = getattr(event, "message", event)
                media = getattr(msg, "media", None)
                is_voice = bool(getattr(msg, "voice", False))
                is_audio = bool(getattr(msg, "audio", False))
                if media is not None and (is_voice or is_audio):
                    try:
                        ext = ".ogg" if is_voice else ".m4a"
                        fd, path = tempfile.mkstemp(suffix=ext)
                        os.close(fd)
                        await self.client.download_media(msg, file=path)
                        payload["attachment_path"] = path
                        payload["attachment_content_type"] = (
                            "audio/ogg" if is_voice else "audio/mpeg"
                        )
                        if not payload["text"]:
                            payload["text"] = ""
                        logger.info(
                            "[telegram] OUTGOING (disparo): to=%s áudio/voice -> Chatwoot",
                            rid,
                        )
                    except Exception as e:
                        logger.warning("[telegram] download media (outgoing) failed: %s", e)
                else:
                    logger.info(
                        "[telegram] OUTGOING (disparo): to=%s (@%s) text=%r -> Chatwoot",
                        rid,
                        username or "-",
                        (payload["text"] or "")[:80],
                    )
                self.bus.emit("telegram.outgoing", payload)
            except Exception as e:
                logger.warning("[telegram] handle_outgoing failed: %s", e)

        # Be gentle
        await asyncio.sleep(2)
        me = await self.client.get_me()
        logger.info(f"[telegram] logged in as {me.username}")

        # Wait before doing anything else
        await asyncio.sleep(1)

        if not await self.client.is_user_authorized():
            logger.warning(
                "[telegram] session '%s' is not authorized. "
                "Authorize once with Telethon to create the session file.",
                self._cfg.session_name,
            )
        logger.info("[telegram] adapter started (native client, text only)")

    async def stop(self) -> None:
        if self.client and self.client.is_connected():
            await self.client.disconnect()
        logger.info("[telegram] adapter stopped")

    async def _resolve_entity(self, raw: str):
        """
        Resolve Telethon 'entity' from a recipient string.
        Supported formats:
          - @username or username
          - phone number (+79991234567)
          - id:<int> or a bare integer (user_id)
        Notes:
          - Sending by phone requires importing the phone into your contacts first.
          - Sending by user_id works only if the session already knows this user
            (i.e., has access_hash cached from previous interactions).
        """
        rid = (raw or "").strip()
        if not rid:
            raise ValueError("recipient_id is empty")

        # Username: Telethon accepts both with and without leading '@'
        if USERNAME_RE.match(rid):
            return rid.lstrip("@")

        # Phone number: import to contacts first, then you can send by the number
        if PHONE_RE.match(rid):
            await self.client(
                functions.contacts.ImportContactsRequest(
                    contacts=[
                        types.InputPhoneContact(
                            client_id=0, phone=rid, first_name="", last_name=""
                        )
                    ]
                )
            )
            return rid

        # Explicit "id:<int>" format
        if rid.startswith("id:"):
            rid = rid[3:].strip()

        # Bare integer: try to resolve as user_id (works only if known to the session)
        if rid.isdigit():
            user_id = int(rid)
            try:
                return await self.client.get_entity(user_id)
            except (ValueError, errors.rpcerrorlist.PeerIdInvalidError):
                raise RuntimeError(
                    "Cannot resolve user by user_id. "
                    "Use @username or phone number (the phone will be imported)."
                )

        # Anything else is not supported
        raise ValueError("recipient_id must be @username, phone number, or id:<int>")

    async def set_typing(self, recipient_id: str, typing: bool = True) -> None:
        """
        Mostra ou cancela o indicador de digitação (typing) para o destinatário.
        Usado pelo endpoint de disparo manual antes de enviar a mensagem.
        """
        if not self.client or not self.client.is_connected():
            return
        try:
            entity = await self._resolve_entity(recipient_id)
            action = "typing" if typing else "cancel"
            await self.client.action(entity, action)
        except Exception as e:
            logger.debug("[telegram] set_typing failed: %s", e)

    async def send_text(self, recipient_id: str, content: TextContent) -> None:
        """
        Send a simple text message, resolving the recipient first.
        Handles common Telegram flood/anti-spam errors.
        """
        if not self.client or not self.client.is_connected():
            logger.warning("[telegram] client is not connected; skipping send")
            return

        try:
            entity = await self._resolve_entity(recipient_id)
            await self.client.send_message(entity, content.text)
            logger.info("[telegram] SENT: %s -> %s", recipient_id, content.text)

        except errors.rpcerrorlist.FloodWaitError as e:
            # Telegram asks to wait N seconds before retry
            logger.error("[telegram] FloodWait: wait %s seconds", e.seconds)
            # Optionally: schedule a delayed retry here

        except errors.rpcerrorlist.PeerFloodError:
            # Too many first messages to unknown users in a short time window
            logger.error("[telegram] PeerFloodError: too many first messages")

        except Exception as e:
            logger.exception("[telegram] Failed to send text: %s", e)

    async def send_media(
        self, recipient_id: str, content: MediaContent
    ) -> None:
        """
        Download media from URL and send to Telegram (voice/audio).
        Used when Chatwoot envia áudio para o contacto.
        """
        if not self.client or not self.client.is_connected():
            logger.warning("[telegram] client is not connected; skipping send_media")
            return

        url = str(content.url).strip()
        if not url:
            logger.warning("[telegram] send_media: url vazia")
            return

        # URL relativa (ex.: Chatwoot /rails/...) precisa de base; por agora assumir absoluta
        path: Optional[str] = None
        try:
            entity = await self._resolve_entity(recipient_id)
            # Chatwoot Active Storage devolve 302; é preciso seguir o redirect
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                r = await client.get(url)
                r.raise_for_status()
                ext = ".ogg" if content.media_type == "audio" else ".m4a"
                fd, path = tempfile.mkstemp(suffix=ext)
                os.close(fd)
                with open(path, "wb") as f:
                    f.write(r.content)
            # Enviar como voice para áudio (nota de voz no Telegram)
            is_voice = content.media_type == "audio"
            await self.client.send_file(entity, path, voice_note=is_voice)
            logger.info(
                "[telegram] SENT MEDIA: %s -> %s (voice=%s)",
                recipient_id,
                content.media_type,
                is_voice,
            )
        except Exception as e:
            logger.exception("[telegram] Failed to send_media: %s", e)
        finally:
            if path and os.path.isfile(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
