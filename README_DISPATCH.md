# Endpoint de Disparo Manual (`/dispatch`) — Telegram

Endpoint extra para enviar mensagem de texto para qualquer destinatário **no Telegram**. Mostra indicador de digitação (typing) pelo tempo que você informar antes de enviar.

## Configuração

1. Defina a variável de ambiente **`DISPATCH_API_TOKEN`** (ex.: um token secreto longo).
2. Se não definir, o endpoint **não fica disponível**.
3. O Telegram precisa estar configurado na aplicação.

## Autenticação

- Header obrigatório: **`Authorization: Bearer <seu_token>`**
- O valor deve ser igual ao `DISPATCH_API_TOKEN`.

## Uso

- **Método:** `POST`
- **URL:** `/dispatch`
- **Headers:** `Authorization: Bearer <DISPATCH_API_TOKEN>`
- **Body (JSON):**
  - `recipient_id`: identificador do destinatário no Telegram (ex.: `@username`, `id:123`)
  - `text`: texto da mensagem
  - `typing_seconds`: (opcional) segundos que o “digitando…” fica ativo antes de enviar; default `2`; use `0` para não mostrar typing

### Exemplo (curl)

```bash
curl -X POST http://localhost:8000/dispatch \
  -H "Authorization: Bearer SEU_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"recipient_id":"@usuario","text":"Olá!","typing_seconds":3}'
```

Sem typing:

```bash
curl -X POST http://localhost:8000/dispatch \
  -H "Authorization: Bearer SEU_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"recipient_id":"@usuario","text":"Olá!","typing_seconds":0}'
```

### Resposta

- **200:** `{"status":"ok","recipient_id":"@usuario"}`
- **401:** falta ou formato errado do header `Authorization`
- **403:** token inválido
- **400:** Telegram não configurado ou parâmetros inválidos

Resumo: só Telegram; configure `DISPATCH_API_TOKEN`, use `Authorization: Bearer <token>` e envie `recipient_id`, `text` e (opcional) `typing_seconds` no body do `POST /dispatch`.
