
# Chatwoot Integrations: WhatsApp, Telegram, VK

This project demonstrates how to integrate [Chatwoot](https://www.chatwoot.com/) with messengers (WhatsApp via Wasender, Telegram via Telethon, VK via Callback API) using Python and FastAPI.

## Features

- Send and receive messages between Chatwoot and:
  - WhatsApp (through Wasender)
  - Telegram (through Telethon, non-bot account)
  - VK (VK group messages)
- Automatic contact sync and enrichment (custom attributes)
- Architecture ready for AI automation and future extensions

## Quick Start

### 1. Prerequisites

- Python 3.11 or newer
- [Poetry](https://python-poetry.org/) for dependency management
- A running instance of Chatwoot
- Wasender account (for WhatsApp integration)
- Telegram account (for Telethon, not a bot)
- VK group and API credentials

### 2. Installation

Clone the repository and install dependencies:

```bash
git clone git@github.com:feel90d/chatwoot-messenger-gateway.git
cd chatwoot-messenger-gateway
poetry install
```

### 3. Configuration

Copy `.env_template` to `.env` and fill in your credentials:

```bash
cp .env_template .env
```

Edit `.env` and set the following sections:

- **Chatwoot**:
  - `CHATWOOT_API_ACCESS_TOKEN`
  - `CHATWOOT_ACCOUNT_ID`
  - `CHATWOOT_BASE_URL`
  - `CHATWOOT_WEBHOOK_ID_WHATSAPP`
  - `CHATWOOT_WEBHOOK_ID_TELEGRAM`
  - `CHATWOOT_WEBHOOK_ID_VK`

- **Wasender**:
  - `WASENDER_API_KEY`
  - `WASENDER_WEBHOOK_SECRET`
  - `WASENDER_WEBHOOK_ID`
  - `WASENDER_INBOX_ID`

- **Telegram**:
  - `TG_API_ID`
  - `TG_API_HASH`
  - `TG_SESSION_NAME` (arbitrary name for your Telethon session)
  - `TG_INBOX_ID`

- **VK**:
  - `VK_ACCESS_TOKEN`
  - `VK_GROUP_ID`
  - `VK_SECRET`
  - `VK_CONFIRMATION`
  - `VK_API_VERSION`
  - `VK_CALLBACK_ID`
  - `VK_INBOX_ID`

> **Note:** All sensitive values must be kept secret. Never commit `.env` to your public repository.

### 4. Running the App

```bash
python ./app/main.py
```

The application exposes a FastAPI server with webhooks for all configured channels. You can use [ngrok](https://ngrok.com/) or another tunnel to expose your local server for webhooks.

Example:

```bash
ngrok http 8000
```

Update your webhook URLs in Chatwoot, Wasender, and VK to point to your public ngrok address.

### 5. How it Works

- **Outgoing:** Messages from Chatwoot are sent to WhatsApp, Telegram, or VK via their respective adapters.
- **Incoming:** Replies from users in messengers are delivered to Chatwoot with all attributes preserved.
- Contacts are matched or created based on messenger IDs (e.g., `telegram_user_id`, `telegram_username`).
- **Webhook por conta:** O webhook do Chatwoot (Settings → Applications → Webhooks) é por conta, portanto recebe eventos de todas as caixas. O gateway filtra por **inbox_id** (TG_INBOX_ID, WASENDER_INBOX_ID, VK_INBOX_ID) e só processa eventos da caixa configurada para cada canal, evitando conflito entre caixas.

### 6. Development

- Format code: `poetry run black .`
- Lint code: `poetry run lint`

## Authors

* [feel90d](mailto:feel90d@gmail.com), Telegram: [@feel90d](https://t.me/feel90d)
* [lukyan0v\_a](mailto:forjob34@gmail.com), Telegram: [@lukyan0v\_a](https://t.me/lukyan0v_a)

If you have any questions, want to discuss integrations, or are interested in a similar solution for your business — feel free to message us on Telegram or by email.
This article was written to share practical experience and help the community — we’re always happy to connect and answer your questions!

## License

MIT (or your preferred license)
