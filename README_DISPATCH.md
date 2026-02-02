# `/dispatch` — Resumo

Endpoint **POST /dispatch** para enviar mensagem de texto para qualquer usuário no **Telegram** (com typing opcional). Só fica ativo se `DISPATCH_API_TOKEN` estiver definido. Em deploy, use a URL do teu domínio (ex.: `https://teu-dominio.com/dispatch`).

---

## Env

```bash
DISPATCH_API_TOKEN=seu_token_secreto
```

- Sem essa variável o endpoint não é registrado.
- Telegram precisa estar configurado (TG_API_ID, TG_API_HASH, sessão, etc.).

---

## Curl

```bash
curl -X POST http://localhost:8000/dispatch \
  -H "Authorization: Bearer seu_token_secreto" \
  -H "Content-Type: application/json" \
  -d '{"recipient_id":"@usuario","text":"Olá!","typing_seconds":2}'
```

- **recipient_id**: obrigatório — ex.: `@username`, `123456789` ou `id:123456789` (só o número também serve)
- **text**: obrigatório — conteúdo da mensagem
- **typing_seconds**: opcional — segundos de “digitando…” antes de enviar (default 2; 0 = sem typing)
- **access_hash**: **obrigatório para pessoas novas** — para enviar por user_id a quem não iniciou conversa, tens de passar o access hash do destinatário (obtém-se noutras APIs/ferramentas). Sem access_hash, o envio por ID só funciona se o user já tiver escrito à conta antes.

Exemplo com **access_hash** (envio por ID sem o user ter escrito primeiro):

```bash
curl -X POST http://localhost:8000/dispatch \
  -H "Authorization: Bearer seu_token_secreto" \
  -H "Content-Type: application/json" \
  -d '{"recipient_id":"6149474306","text":"Olá!","access_hash":1234567890123456789}'
```

Resposta **200** só quando a mensagem for enviada com sucesso. **503** quando o envio falhar. 401/403 = auth; 400 = parâmetros inválidos.

---

## Implementação (resumo)

- **Config:** `app/config.py` — `dispatch_api_token` opcional (lido de `DISPATCH_API_TOKEN`).
- **HTTP:** `app/delivery/http.py` — POST `/dispatch` com validação `Authorization: Bearer <token>`; body `recipient_id`, `text`, `typing_seconds`; chama `message_router.dispatch_direct(..., typing_seconds=...)`.
- **Router:** `app/application/router.py` — `dispatch_direct(channel="telegram", recipient_id, text, typing_seconds)`; se `typing_seconds > 0`, chama `adapter.set_typing` e `asyncio.sleep(typing_seconds)`; depois `adapter.send_text`.
- **Adapter:** `app/infra/adapters/telegram_telethon.py` — `set_typing(recipient_id, typing)` com `client.action(entity, "typing"|"cancel")`.
- **Main:** `app/main.py` — passa `message_router=router` para `create_router` para o endpoint ser registrado.
