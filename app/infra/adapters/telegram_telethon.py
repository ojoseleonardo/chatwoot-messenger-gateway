import asyncio
import base64
import logging
import os
import re
from typing import Optional

from pyee.asyncio import AsyncIOEventEmitter
from telethon import TelegramClient, errors, events, functions, types

from app.config import TelegramConfig
from app.domain.message import TextContent
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
        # Coolify / deploy sem terminal: sessão pronta via TG_SESSION_BASE64
        session_b64 = os.getenv("TG_SESSION_BASE64", "").strip()
        if session_b64:
            session_path = os.path.join(
                os.getcwd(), f"{self._cfg.session_name}.session"
            )
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

        self.client = TelegramClient(
            self._cfg.session_name,
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
                "text": event.text,
                "from_id": str(from_id) if from_id else None,
                "username": username,
                "name": first_name or username or str(from_id),
            }
            # Emit telegram.incoming event to the bus
            self.bus.emit("telegram.incoming", payload)

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
