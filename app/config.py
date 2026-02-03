import os
from typing import Dict, Optional

from pydantic import BaseModel, Field, HttpUrl, ValidationError


class TelegramConfig(BaseModel):
    api_id: int
    api_hash: str
    session_name: str
    inbox_id: int  # per-channel inbox


class WasenderWebhookConfig(BaseModel):
    webhook_id: str
    webhook_secret: str
    api_key: str
    inbox_id: int  # per-channel inbox


class VKCommunityConfig(BaseModel):
    # VK community configuration for Callback API and sending messages
    callback_id: str  # Unique callback ID for path-based security
    group_id: int  # VK group ID (without minus)
    access_token: str  # VK community access token
    secret: str  # Secret key for callback signature verification
    confirmation: str  # Confirmation string from VK
    api_version: str = "5.199"  # VK API version
    inbox_id: int  # per-channel inbox


class ChatwootWebhookConfig(BaseModel):
    api_access_token: str
    account_id: int
    base_url: HttpUrl
    # Map webhook id -> channel name
    channel_by_webhook_id: Dict[str, str] = Field(default_factory=dict)
    # Map channel name -> inbox_id (para filtrar webhooks por caixa de entrada)
    inbox_id_by_channel: Dict[str, int] = Field(default_factory=dict)


class AppConfig(BaseModel):
    telegram: Optional[TelegramConfig] = None
    wasender: Optional[WasenderWebhookConfig] = None
    vk: Optional[VKCommunityConfig] = None
    chatwoot: ChatwootWebhookConfig
    # Token para o endpoint de disparo manual (DISPATCH_API_TOKEN). Se vazio, o endpoint fica desativado.
    dispatch_api_token: Optional[str] = None


def _getenv(name: str) -> str:
    """Get required environment variable or raise RuntimeError."""
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return v


def _build_channel_map() -> Dict[str, str]:
    """Build a map from webhook ID to channel name."""
    mapping: Dict[str, str] = {}
    w = os.getenv("CHATWOOT_WEBHOOK_ID_WHATSAPP")
    t = os.getenv("CHATWOOT_WEBHOOK_ID_TELEGRAM")
    v = os.getenv("CHATWOOT_WEBHOOK_ID_VK")
    if w:
        mapping[w] = "whatsapp"
    if t:
        mapping[t] = "telegram"
    if v:
        mapping[v] = "vk"
    return mapping


def load_config() -> AppConfig:
    try:
        # Telegram config: TG_API_ID + TG_API_HASH + TG_INBOX_ID; TG_SESSION_NAME opcional (default: session → session.session)
        if os.getenv("TG_API_ID") and os.getenv("TG_API_HASH") and os.getenv("TG_INBOX_ID"):
            telegram_cfg = TelegramConfig(
                api_id=int(_getenv("TG_API_ID")),
                api_hash=_getenv("TG_API_HASH"),
                session_name=os.getenv("TG_SESSION_NAME", "").strip() or "session",
                inbox_id=int(_getenv("TG_INBOX_ID")),
            )
        else:
            telegram_cfg = None

        # Wasender config: only if all variables are present
        if (
            os.getenv("WASENDER_WEBHOOK_ID")
            and os.getenv("WASENDER_WEBHOOK_SECRET")
            and os.getenv("WASENDER_API_KEY")
        ):
            wasender_cfg = WasenderWebhookConfig(
                webhook_id=_getenv("WASENDER_WEBHOOK_ID"),
                webhook_secret=_getenv("WASENDER_WEBHOOK_SECRET"),
                api_key=_getenv("WASENDER_API_KEY"),
                inbox_id=int(os.getenv("WASENDER_INBOX_ID")),
            )
        else:
            wasender_cfg = None

        # VK: create config only if all required variables are present
        if (
            os.getenv("VK_CALLBACK_ID")
            and os.getenv("VK_GROUP_ID")
            and os.getenv("VK_ACCESS_TOKEN")
            and os.getenv("VK_SECRET")
            and os.getenv("VK_CONFIRMATION")
        ):
            vk_cfg = VKCommunityConfig(
                callback_id=_getenv("VK_CALLBACK_ID"),
                group_id=int(_getenv("VK_GROUP_ID")),
                access_token=_getenv("VK_ACCESS_TOKEN"),
                secret=_getenv("VK_SECRET"),
                confirmation=_getenv("VK_CONFIRMATION"),
                api_version=os.getenv("VK_API_VERSION") or "5.199",
                inbox_id=int(os.getenv("VK_INBOX_ID")),
            )
        else:
            vk_cfg = None

        dispatch_token = (os.getenv("DISPATCH_API_TOKEN") or "").strip() or None

        # Mapa canal -> inbox_id para filtrar webhooks por caixa (evitar conflito entre caixas)
        inbox_by_channel: Dict[str, int] = {}
        if telegram_cfg:
            inbox_by_channel["telegram"] = telegram_cfg.inbox_id
        if wasender_cfg:
            inbox_by_channel["whatsapp"] = wasender_cfg.inbox_id
        if vk_cfg:
            inbox_by_channel["vk"] = vk_cfg.inbox_id

        return AppConfig(
            telegram=telegram_cfg,
            wasender=wasender_cfg,
            vk=vk_cfg,
            chatwoot=ChatwootWebhookConfig(
                api_access_token=_getenv("CHATWOOT_API_ACCESS_TOKEN"),
                account_id=int(_getenv("CHATWOOT_ACCOUNT_ID")),
                base_url=_getenv("CHATWOOT_BASE_URL"),
                channel_by_webhook_id=_build_channel_map(),
                inbox_id_by_channel=inbox_by_channel,
            ),
            dispatch_api_token=dispatch_token,
        )
    except ValidationError as e:
        raise RuntimeError(f"Invalid configuration: {e}") from e
