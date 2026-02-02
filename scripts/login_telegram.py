"""
Login no Telegram e grava a sessão num ficheiro .session (para uso local).
Executa uma vez: pede telefone e código no terminal e guarda a sessão.

Uso (na raiz do projeto, com .env ou variáveis definidas):
  poetry run python scripts/login_telegram.py

Variáveis: TG_API_ID, TG_API_HASH; opcional: TG_SESSION_NAME (default: session)
"""
import asyncio
import os
import sys

# carregar .env da raiz do projeto
try:
    from dotenv import load_dotenv
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(root, ".env"))
except ImportError:
    pass

from telethon import TelegramClient


def main():
    api_id = os.getenv("TG_API_ID")
    api_hash = os.getenv("TG_API_HASH")
    session_name = (os.getenv("TG_SESSION_NAME") or "session").strip()

    if not api_id or not api_hash:
        print("Define TG_API_ID e TG_API_HASH (em .env ou no ambiente).", file=sys.stderr)
        sys.exit(1)

    async def run():
        client = TelegramClient(
            session_name,
            int(api_id),
            api_hash,
            device_model="iPhone 14",
            system_version="16.5",
            app_version="8.4.1",
        )
        await client.start()
        me = await client.get_me()
        await client.disconnect()
        print(f"Sessão gravada em: {session_name}.session")
        print(f"Conta: {me.first_name or me.username or me.phone}")

    asyncio.run(run())


if __name__ == "__main__":
    main()
