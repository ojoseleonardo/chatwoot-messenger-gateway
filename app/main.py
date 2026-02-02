import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from pyee.asyncio import AsyncIOEventEmitter

from app.application.events import wire_events
from app.application.router import MessageRouter
from app.config import load_config
from app.delivery.http import create_router
from app.infra.adapters.telegram_telethon import TelegramAdapter
from app.infra.adapters.vk_bot import VkAdapter
from app.infra.adapters.whatsapp_wasender import WasenderAdapter

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)

# Load env and config
load_dotenv()
config = load_config()

# Shared event bus
bus = AsyncIOEventEmitter()

# Build adapters registry only for configured channels
adapters: Dict[str, Any] = {}

if config.wasender:
    adapters["whatsapp"] = WasenderAdapter(bus=bus, config=config.wasender)

if config.telegram:
    adapters["telegram"] = TelegramAdapter(bus=bus, config=config.telegram)

if config.vk:
    adapters["vk"] = VkAdapter(bus=bus, config=config.vk)

router = MessageRouter(
    adapters=adapters,
    chatwoot_base_url=str(config.chatwoot.base_url),
)

# Wire adapter incoming → application router (existing behavior)
for a in adapters.values():
    a.on_message(router.handle_incoming)

# Wire bus event handlers (moved out of main into application layer)
wire_events(bus=bus, config=config, adapters=adapters, router=router)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Log here (server process only; avoids duplicate logs from reloader)
    logging.info("adapters configured: %s", list(adapters.keys()))
    await asyncio.gather(
        *(a.start() for a in adapters.values()), return_exceptions=True
    )
    try:
        yield
    finally:
        await asyncio.gather(
            *(a.stop() for a in adapters.values()), return_exceptions=True
        )


app = FastAPI(title="Messaging Bridge", version="0.1.0", lifespan=lifespan)
app.include_router(create_router(bus=bus, config=config))

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, log_level="info")
