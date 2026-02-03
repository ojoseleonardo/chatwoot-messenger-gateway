import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from pyee.asyncio import AsyncIOEventEmitter
from starlette.responses import PlainTextResponse

from app.application.router import MessageRouter
from app.config import AppConfig
from app.domain.webhooks.wasender import WasenderWebhookPayload

logger = logging.getLogger(__name__)


class DispatchBody(BaseModel):
    """Corpo do endpoint de disparo manual (apenas Telegram): destinatário, texto e tempo de typing."""

    recipient_id: str = Field(..., min_length=1, description="ID do destinatário (ex: @user, 6149474306)")
    text: str = Field(..., min_length=1, description="Texto da mensagem")
    typing_seconds: float = Field(
        default=2.0,
        ge=0,
        le=60,
        description="Segundos que o indicador de digitação fica ativo antes de enviar (0 = sem typing)",
    )
    access_hash: Optional[int] = Field(
        default=None,
        description="Access hash do destinatário (Telegram). Obrigatório para enviar para pessoas novas (que não iniciaram conversa); sem ele o envio por user_id falha.",
    )

    @field_validator("recipient_id", mode="before")
    @classmethod
    def recipient_id_to_str(cls, v: object) -> str:
        """Aceita número (ex.: n8n envia 6149474306) e converte para string."""
        if v is None:
            return ""
        return str(v).strip()

    @field_validator("access_hash", mode="before")
    @classmethod
    def access_hash_to_int(cls, v: object) -> Optional[int]:
        """Aceita número ou string numérica (ex.: do n8n) e converte para int."""
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None


def create_router(
    bus: AsyncIOEventEmitter,
    config: AppConfig,
    message_router: Optional[MessageRouter] = None,
) -> APIRouter:
    """
    Build HTTP routes with simple security checks.
    """
    router = APIRouter(tags=["webhooks"])

    @router.get("/health")
    async def health():
        # Report only non-sensitive fields
        wasender_enabled = bool(getattr(config, "wasender", None))
        telegram_enabled = bool(getattr(config, "telegram", None))
        vk_enabled = bool(getattr(config, "vk", None))

        return {
            "ok": True,
            "chatwoot": {
                "account_id": config.chatwoot.account_id,
                "base_url": str(config.chatwoot.base_url),
                "channels_configured": list(
                    config.chatwoot.channel_by_webhook_id.values()
                ),
            },
            "wasender": {
                "enabled": wasender_enabled,
            },
            "telegram": {
                "enabled": telegram_enabled,
                "session_name": (
                    config.telegram.session_name if telegram_enabled else None
                ),
            },
            "vk": {
                "enabled": vk_enabled,
                # Do not expose callback_id/secret/token; group_id is safe to show
                "group_id": config.vk.group_id if vk_enabled else None,
            },
        }

    @router.post("/wasender/webhook/{webhook_id}", response_model=dict)
    async def wasender_webhook(
        webhook_id: str,
        payload: WasenderWebhookPayload,
        x_webhook_signature: str | None = Header(
            default=None, alias="X-Webhook-Signature"
        ),
    ):
        # Verify path token first
        if webhook_id != config.wasender.webhook_id:
            raise HTTPException(status_code=403, detail="Invalid webhook ID")
        # Simple header equality check (no HMAC)
        if x_webhook_signature != config.wasender.webhook_secret:
            raise HTTPException(status_code=403, detail="Invalid X-Webhook-Signature")

        event = payload.event
        logger.info("[http] Wasender webhook accepted: event=%s", event)

        if event == "messages.upsert":
            try:
                raw = payload.data["messages"]
                key = raw["key"]
                from_me = key["fromMe"]
                bus.emit(
                    "wasender.outgoing" if from_me else "wasender.incoming",
                    payload.model_dump(),
                )
            except Exception as e:
                raise HTTPException(
                    status_code=400, detail=f"Invalid upsert format: {e}"
                )
        else:
            logger.info("[wasender] Ignored event: %s", event)

        return {"status": "ok"}

    @router.post("/chatwoot/webhook/{webhook_id}", response_model=dict)
    async def chatwoot_webhook(webhook_id: str, request: Request):
        # Determine channel by webhook_id (per-channel hooks) or fallback to legacy id
        channel = config.chatwoot.channel_by_webhook_id.get(webhook_id)
        if not channel:
            raise HTTPException(status_code=403, detail="Unknown webhook ID")

        payload = await request.json()
        event = payload.get("event")
        msg_type = payload.get("message_type")

        # Filtrar por caixa de entrada: o webhook é por conta (Applications → Webhooks),
        # então recebemos eventos de todas as caixas; processar só os da caixa deste canal.
        conv = payload.setdefault("conversation", {})
        payload_inbox_raw = conv.get("inbox_id") or (conv.get("inbox") or {}).get("id")
        expected_inbox = config.chatwoot.inbox_id_by_channel.get(channel)
        if expected_inbox is not None and payload_inbox_raw is not None:
            try:
                payload_inbox = int(payload_inbox_raw)
                if payload_inbox != expected_inbox:
                    logger.info(
                        "[chatwoot] Event ignored: inbox_id=%s does not match channel %s (expected inbox=%s)",
                        payload_inbox,
                        channel,
                        expected_inbox,
                    )
                    return {"status": "received"}
            except (TypeError, ValueError):
                pass

        meta = conv.setdefault("meta", {})
        if channel:
            # Inject resolved channel so downstream router can dispatch
            meta["channel"] = channel

        logger.info(
            "[http] Chatwoot webhook accepted: event=%s type=%s channel=%s",
            event,
            msg_type,
            meta.get("channel"),
        )

        if event == "message_created":
            if msg_type == "incoming":
                bus.emit("chatwoot.incoming", payload)
            elif msg_type == "outgoing":
                bus.emit("chatwoot.outgoing", payload)
            else:
                logger.warning("[chatwoot] Unknown message_type: %s", msg_type)
        else:
            logger.info("[chatwoot] Ignored event: %s", event)

        return {"status": "received"}

    @router.post("/vk/callback/{callback_id}", response_class=PlainTextResponse)
    async def vk_callback(callback_id: str, request: Request) -> PlainTextResponse:
        """
        VK Callback endpoint with path-based security and confirmation support.

        - Verifies path callback_id first.
        - On 'confirmation' returns confirmation token (no secret required).
        - On other events verifies 'secret' and 'group_id'.
        - Emits 'vk.incoming' on 'message_new' and 'vk.confirmation' on confirmation.
        - Responds with plain text as VK requires.
        """
        if not getattr(config, "vk", None):
            raise HTTPException(status_code=503, detail="VK adapter is not configured")

        # Verify callback_id from path
        if callback_id != config.vk.callback_id:
            raise HTTPException(status_code=403, detail="Invalid callback ID")

        try:
            payload: Dict[str, Any] = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        event_type = payload.get("type")
        group_id = payload.get("group_id")
        secret = payload.get("secret")

        logger.info("[vk] event received: type=%s group_id=%s", event_type, group_id)

        # Handle confirmation (no secret required)
        if event_type == "confirmation":
            if group_id != config.vk.group_id:
                raise HTTPException(status_code=400, detail="Invalid group_id")
            # Optional: emit confirmation event for debugging/metrics
            bus.emit("vk.confirmation", {"group_id": group_id})
            return PlainTextResponse(config.vk.confirmation)

        # For all other events, verify secret and group_id
        if secret != config.vk.secret:
            raise HTTPException(status_code=403, detail="Invalid secret")
        if group_id != config.vk.group_id:
            raise HTTPException(status_code=400, detail="Invalid group_id")

        if event_type == "message_new":
            try:
                obj = payload.get("object") or {}
                message = obj.get("message") or {}
                # Emit unified internal event; VkAdapter will convert to UnifiedMessage
                bus.emit(
                    "vk.incoming",
                    {"event": "message_new", "message": message, "raw": payload},
                )
            except Exception as e:
                raise HTTPException(
                    status_code=400, detail=f"Invalid message_new payload: {e}"
                )
        else:
            # Acknowledge other events to prevent VK retries
            logger.info("[vk] ignored event type: %s", event_type)

        # VK requires literal 'ok' to acknowledge processing
        return PlainTextResponse("ok")

    # Endpoint de disparo manual (requer DISPATCH_API_TOKEN)
    if config.dispatch_api_token and message_router is not None:

        def _check_dispatch_token(authorization: str | None = Header(default=None)):
            if not authorization or not authorization.startswith("Bearer "):
                raise HTTPException(
                    status_code=401,
                    detail="Header Authorization: Bearer <token> obrigatório",
                )
            token = authorization[7:].strip()
            if token != config.dispatch_api_token:
                raise HTTPException(status_code=403, detail="Token inválido")

        @router.post("/dispatch", response_model=dict)
        async def dispatch(
            body: DispatchBody,
            authorization: str | None = Header(default=None, alias="Authorization"),
        ):
            """
            Envia uma mensagem de texto para qualquer destinatário no Telegram.
            Mostra indicador de digitação (typing) pelo tempo informado em typing_seconds antes de enviar.
            Autenticação: header Authorization: Bearer <DISPATCH_API_TOKEN>.
            """
            _check_dispatch_token(authorization)
            try:
                await message_router.dispatch_direct(
                    channel="telegram",
                    recipient_id=body.recipient_id.strip(),
                    text=body.text.strip(),
                    typing_seconds=body.typing_seconds,
                    access_hash=body.access_hash,
                )
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            except (RuntimeError, Exception) as e:
                msg = str(e)
                # Sessão Telegram invalidada (utilizador terminou sessões no app)
                if "authorization has been invalidated" in msg or "terminating all sessions" in msg:
                    msg = (
                        "Sessão do Telegram invalidada (a conta desconectou todos os dispositivos). "
                        "Faça login de novo (ex.: scripts/login_telegram.py), atualize o ficheiro/campo de sessão e reinicie o gateway."
                    )
                raise HTTPException(
                    status_code=503,
                    detail=msg,
                ) from e
            return {"status": "ok", "recipient_id": body.recipient_id}

    return router
