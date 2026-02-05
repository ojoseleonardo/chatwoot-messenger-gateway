import asyncio
import base64
import logging
import os
import random
import re
import tempfile
from typing import Optional, Tuple, Union

from pyee.asyncio import AsyncIOEventEmitter
from telethon import TelegramClient, errors, events, functions, types

import httpx

from app.config import TelegramConfig
from app.domain.message import MediaContent, TextContent
from app.domain.ports import MessengerAdapter, OnMessage

logger = logging.getLogger(__name__)

# Configurações de typing para simular digitação humana
# Digitadores médios: 40-60 palavras por minuto (200-300 caracteres/minuto)
# Digitadores rápidos: 60-80 palavras por minuto (300-400 caracteres/minuto)
# Usamos ~10 caracteres por segundo (60 palavras/min aproximadamente)
TYPING_CHARS_PER_SECOND = 10
TYPING_VARIATION_PERCENT = 0.15  # ±15% variação aleatória

# Configurações de "gravando áudio": +4s preparação; ±5% variação (mesma lógica do WhatsApp)
RECORD_AUDIO_EXTRA_SECONDS = 4
RECORD_AUDIO_VARIATION_PERCENT = 0.05  # ±5%
# Fallback por transcrição: ~19 caracteres/s (velocidade de fala ElevenLabs)
RECORD_AUDIO_CHARS_PER_SECOND = 19


def get_audio_duration_seconds(file_path: str) -> Optional[float]:
    """
    Obtém a duração real do áudio em segundos a partir do ficheiro (ogg, m4a, mp3).
    Usa mutagen para ler metadados. Retorna None se não conseguir.
    """
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(file_path)
        if audio is not None and hasattr(audio, "info") and hasattr(audio.info, "length"):
            return float(audio.info.length)
    except Exception:  # mutagen não instalado, formato não suportado, ficheiro inválido
        pass
    return None


def record_audio_delay_from_duration(duration_seconds: float) -> float:
    """Duração do indicador 'gravando': duração real + 4s preparação, com ±5% variação."""
    base = duration_seconds + RECORD_AUDIO_EXTRA_SECONDS
    variation = (random.random() * 2 * RECORD_AUDIO_VARIATION_PERCENT) - RECORD_AUDIO_VARIATION_PERCENT
    return base * (1 + variation)


def record_audio_delay_from_transcript(transcript: str) -> float:
    """Fallback: tempo 'gravando' baseado na transcrição (19 chars/s, ±5%, +4s)."""
    char_count = len(transcript) if transcript else 0
    if char_count == 0:
        return RECORD_AUDIO_EXTRA_SECONDS
    base_seconds = char_count / RECORD_AUDIO_CHARS_PER_SECOND
    variation = (random.random() * 2 * RECORD_AUDIO_VARIATION_PERCENT) - RECORD_AUDIO_VARIATION_PERCENT
    return base_seconds * (1 + variation) + RECORD_AUDIO_EXTRA_SECONDS


def calculate_typing_delay(text: str) -> Tuple[float, int, int]:
    """
    Calcula o tempo de digitação baseado no número de caracteres.
    
    Segue a mesma lógica usada no WhatsApp:
    - ~10 caracteres por segundo (60 palavras/min)
    - Variação aleatória de ±15% para parecer mais humano
    - Sem limite máximo (mensagens grandes = tempo maior)
    
    Returns:
        Tuple[float, int, int]: (total_seconds, seconds, milliseconds)
    """
    char_count = len(text) if text else 0
    if char_count == 0:
        return (0.0, 0, 0)
    
    # Calcula o tempo base em segundos (charCount / charsPerSecond)
    base_seconds = char_count / TYPING_CHARS_PER_SECOND
    
    # Adiciona uma variação aleatória de ±15% para parecer mais humano
    # variation = (Math.random() * 0.3) - 0.15 => -0.15 a +0.15
    variation = (random.random() * 0.3) - 0.15
    total_seconds = base_seconds * (1 + variation)
    
    # Separa segundos e milissegundos
    seconds = int(total_seconds)
    milliseconds = round((total_seconds - seconds) * 1000)
    
    return (total_seconds, seconds, milliseconds)

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
        # Offset para iterar membros do grupo (usado pelo endpoint /telegram/members/next)
        self._members_offset: int = 0
        # IDs já retornados (para não repetir)
        self._members_returned: set[int] = set()

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
            # Ignorar mensagens de grupos/canais; só encaminhar conversas privadas (1:1)
            peer = getattr(event, "peer_id", None) or getattr(
                getattr(event, "message", None), "peer_id", None
            )
            if peer is None or not isinstance(peer, types.PeerUser):
                return
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

        # Log se TG_GROUP_INVITE está configurado (usado pelo endpoint /telegram/members/next)
        group_invite = (os.getenv("TG_GROUP_INVITE") or "").strip()
        if group_invite:
            logger.info("[telegram] TG_GROUP_INVITE configured — /telegram/members/next endpoint available")
        else:
            logger.info("[telegram] TG_GROUP_INVITE not set — /telegram/members/next endpoint will not work")

        logger.info("[telegram] adapter started (native client, text only)")

    async def stop(self) -> None:
        if self.client and self.client.is_connected():
            await self.client.disconnect()
        logger.info("[telegram] adapter stopped")

    def get_status(self) -> dict:
        """Retorna estado do adapter para diagnóstico (usado no /health)."""
        return {
            "connected": self.client.is_connected() if self.client else False,
            "members_returned": len(self._members_returned),
        }

    async def get_next_member(self) -> Optional[dict]:
        """
        Retorna o próximo membro do grupo (TG_GROUP_INVITE) que ainda não foi retornado.
        Devolve dict com user_id, access_hash, username, first_name, last_name, phone.
        Ao chamar este método, a sessão "vê" o user e fica com access_hash para enviar DM.
        """
        if not self.client or not self.client.is_connected():
            raise RuntimeError("Cliente Telegram não está conectado")

        group_invite = (os.getenv("TG_GROUP_INVITE") or "").strip()
        if not group_invite:
            raise RuntimeError("TG_GROUP_INVITE não configurado")

        try:
            group = await self.client.get_entity(group_invite)
            async for participant in self.client.iter_participants(group):
                pid = getattr(participant, "id", None)
                if pid is None:
                    continue
                # Já retornado antes? Pular
                if pid in self._members_returned:
                    continue
                # Marcar como retornado
                self._members_returned.add(pid)
                # Construir resposta (user_id e access_hash como string para evitar
                # perda de precisão em JSON/JavaScript com números de 64 bits)
                access_hash_raw = getattr(participant, "access_hash", None)
                return {
                    "user_id": str(pid),
                    "access_hash": str(access_hash_raw) if access_hash_raw is not None else None,
                    "username": getattr(participant, "username", None),
                    "first_name": getattr(participant, "first_name", None),
                    "last_name": getattr(participant, "last_name", None),
                    "phone": getattr(participant, "phone", None),
                }
            # Todos os membros já foram retornados
            return None
        except Exception as e:
            logger.exception("[telegram] get_next_member failed: %s", e)
            raise RuntimeError(f"Falha ao obter próximo membro: {e}") from e

    def reset_members_iterator(self) -> int:
        """Reinicia o iterador de membros. Retorna quantos tinham sido retornados antes do reset."""
        count = len(self._members_returned)
        self._members_returned.clear()
        logger.info("[telegram] members iterator reset (was at %s)", count)
        return count

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
          - Para enviar por user_id a users desconhecidos, usar o endpoint /telegram/members/next
            para "ativar" o user primeiro, e depois chamar /dispatch com access_hash.
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

        # Bare integer: try to resolve as user_id (works only if session knows user)
        if rid.isdigit():
            user_id = int(rid)
            try:
                return await self.client.get_entity(user_id)
            except (ValueError, errors.rpcerrorlist.PeerIdInvalidError):
                logger.warning("[telegram] get_entity(%s) failed — user not in session cache", user_id)
                raise RuntimeError(
                    f"Destinatário '{rid}' não encontrado na sessão. "
                    "Use o endpoint /telegram/members/next para obter o access_hash e envie com access_hash no /dispatch."
                )

        # Anything else is not supported
        raise ValueError("recipient_id must be @username, phone number, or id:<int>")

    def _entity_for_dispatch(
        self, recipient_id: str, access_hash: Optional[int] = None
    ) -> Optional[types.InputPeerUser]:
        """
        Se tiver access_hash e recipient_id numérico, devolve InputPeerUser.
        Caso contrário devolve None (usa _resolve_entity).
        """
        if access_hash is None:
            return None
        rid = (recipient_id or "").strip()
        if rid.startswith("id:"):
            rid = rid[3:].strip()
        if not rid.isdigit():
            return None
        return types.InputPeerUser(int(rid), access_hash)

    async def set_typing(
        self, recipient_id: str, typing: bool = True, access_hash: Optional[int] = None
    ) -> None:
        """
        Mostra ou cancela o indicador de digitação (typing) para o destinatário.
        access_hash: opcional; permite usar user_id sem o user ter iniciado conversa.
        """
        if not self.client or not self.client.is_connected():
            return
        try:
            entity: Union[types.InputPeerUser, object] = self._entity_for_dispatch(
                recipient_id, access_hash
            )
            if entity is None:
                entity = await self._resolve_entity(recipient_id)
            # Usar API de baixo nível SetTypingRequest
            action = (
                types.SendMessageTypingAction()
                if typing
                else types.SendMessageCancelAction()
            )
            await self.client(
                functions.messages.SetTypingRequest(
                    peer=entity,
                    action=action,
                )
            )
        except Exception as e:
            logger.debug("[telegram] set_typing failed: %s", e)

    async def _mark_as_read(self, entity: Union[types.InputPeerUser, object]) -> None:
        """
        Marca a conversa como lida (envia "lido" / read receipt) para o destinatário.
        Só utilizadores (não bots) podem usar. Falhas são ignoradas.
        """
        if not self.client or not self.client.is_connected():
            return
        try:
            # Obter a última mensagem no chat para usar como max_id
            last = await self.client.get_messages(entity, limit=1)
            max_id = last[0].id if last else 0
            if max_id <= 0:
                return
            await self.client(
                functions.messages.ReadHistoryRequest(
                    peer=entity,
                    max_id=max_id,
                )
            )
            logger.debug("[telegram] marked as read (max_id=%s)", max_id)
        except Exception as e:
            logger.debug("[telegram] mark as read failed: %s", e)

    async def send_text(
        self,
        recipient_id: str,
        content: TextContent,
        access_hash: Optional[int] = None,
        *,
        mark_as_gateway_send: bool = True,
        simulate_typing: bool = True,
    ) -> None:
        """
        Send a simple text message, resolving the recipient first.
        access_hash: opcional; permite enviar por user_id sem o user ter iniciado conversa.
        mark_as_gateway_send: se True (envio pelo webhook Chatwoot), evita duplicar no Chatwoot;
            se False (envio pelo /dispatch), o handler telegram.outgoing cria a msg no Chatwoot.
        simulate_typing: se True (padrão), mostra indicador de digitação antes de enviar,
            com duração baseada no número de caracteres da mensagem.
        Raises on failure so callers (e.g. /dispatch) can return error to the client.
        """
        if not self.client or not self.client.is_connected():
            raise RuntimeError("Cliente Telegram não está conectado")

        entity = self._entity_for_dispatch(recipient_id, access_hash)
        if entity is None:
            entity = await self._resolve_entity(recipient_id)

        try:
            # Marcar como lido antes do typing (o destinatário vê o "lido")
            await self._mark_as_read(entity)

            # Simular typing baseado no número de caracteres (parecer mais humano)
            if simulate_typing and content.text:
                total_seconds, seconds, milliseconds = calculate_typing_delay(content.text)
                if total_seconds > 0:
                    logger.info(
                        "[telegram] typing for %.2fs (%d chars) before sending to %s",
                        total_seconds,
                        len(content.text),
                        recipient_id,
                    )
                    # Telegram cancela o typing após ~5s se não for reenviado.
                    # Reenviar a cada 4s para manter o indicador visível durante todo o tempo.
                    try:
                        elapsed = 0.0
                        while elapsed < total_seconds:
                            await self.client(
                                functions.messages.SetTypingRequest(
                                    peer=entity,
                                    action=types.SendMessageTypingAction(),
                                )
                            )
                            chunk = min(4.0, total_seconds - elapsed)
                            await asyncio.sleep(chunk)
                            elapsed += chunk
                    except Exception as typing_err:
                        logger.warning("[telegram] typing failed (continuing): %s", typing_err)

            await self.client.send_message(entity, content.text)
            # Só marcar como "enviado pelo gateway" quando for webhook Chatwoot (não /dispatch)
            if mark_as_gateway_send:
                resolved_id = getattr(entity, "user_id", None) or getattr(entity, "id", None)
                if resolved_id is not None and self.bus:
                    self.bus.emit(
                        "telegram.sent_by_gateway",
                        {"to_id": str(resolved_id), "text": content.text},
                    )
            logger.info("[telegram] SENT: %s -> %s", recipient_id, content.text)
        except ValueError as e:
            if "Cannot find any entity" in str(e) or "corresponding to" in str(e):
                raise RuntimeError(
                    f"Destinatário '{recipient_id}' não encontrado. "
                    "O utilizador precisa ter iniciado conversa com esta conta (Telegram) antes."
                ) from e
            raise
        except errors.rpcerrorlist.FloodWaitError as e:
            logger.error("[telegram] FloodWait: wait %s seconds", e.seconds)
            raise RuntimeError(f"Telegram FloodWait: aguardar {e.seconds}s antes de reenviar") from e
        except errors.rpcerrorlist.PeerFloodError:
            logger.error("[telegram] PeerFloodError: too many first messages")
            raise RuntimeError(
                "Telegram PeerFlood: demasiadas primeiras mensagens; aguardar antes de reenviar"
            ) from None
        except Exception as e:
            logger.exception("[telegram] Failed to send text: %s", e)
            raise

    async def send_media(
        self,
        recipient_id: str,
        content: MediaContent,
        *,
        simulate_typing: bool = True,
    ) -> None:
        """
        Download media from URL and send to Telegram (voice/audio).
        Used when Chatwoot envia áudio para o contacto.
        simulate_typing: se True (padrão), mostra indicador de "gravando áudio" antes de enviar.
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

            # Marcar como lido antes do gravando áudio (o destinatário vê o "lido")
            await self._mark_as_read(entity)

            # Descarregar o ficheiro primeiro (precisamos dele para obter duração do áudio)
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                r = await client.get(url)
                r.raise_for_status()
                ext = ".ogg" if content.media_type == "audio" else ".m4a"
                fd, path = tempfile.mkstemp(suffix=ext)
                os.close(fd)
                with open(path, "wb") as f:
                    f.write(r.content)

            # Simular "gravando áudio": 1) duração real 2) fallback transcrição 3) fixo
            if simulate_typing:
                if content.media_type == "audio":
                    duration = get_audio_duration_seconds(path)
                    if duration is not None:
                        total_seconds = record_audio_delay_from_duration(duration)
                        logger.info(
                            "[telegram] typing (record_audio) for %.2fs — usado: duração real do ficheiro (%.1fs) — before sending to %s",
                            total_seconds,
                            duration,
                            recipient_id,
                        )
                    elif content.transcript:
                        total_seconds = record_audio_delay_from_transcript(content.transcript)
                        logger.info(
                            "[telegram] typing (record_audio) for %.2fs — usado: transcrição (%d chars) — before sending to %s",
                            total_seconds,
                            len(content.transcript),
                            recipient_id,
                        )
                    else:
                        total_seconds = 1.0 + random.random()
                        logger.info(
                            "[telegram] typing (record_audio) for %.2fs — usado: fallback fixo (duração e transcrição indisponíveis) — before sending to %s",
                            total_seconds,
                            recipient_id,
                        )
                else:
                    total_seconds = 1.0 + random.random()
                    logger.info(
                        "[telegram] typing for %.2fs before sending media to %s",
                        total_seconds,
                        recipient_id,
                    )
                try:
                    action = (
                        types.SendMessageRecordAudioAction()
                        if content.media_type == "audio"
                        else types.SendMessageTypingAction()
                    )
                    elapsed = 0.0
                    while elapsed < total_seconds:
                        await self.client(
                            functions.messages.SetTypingRequest(
                                peer=entity,
                                action=action,
                            )
                        )
                        chunk = min(4.0, total_seconds - elapsed)
                        await asyncio.sleep(chunk)
                        elapsed += chunk
                except Exception as typing_err:
                    logger.warning("[telegram] typing failed (continuing): %s", typing_err)

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
