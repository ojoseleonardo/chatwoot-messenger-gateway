# Variáveis de Ambiente Necessárias

Este documento lista todas as variáveis de ambiente necessárias para configurar o Chatwoot Messenger Gateway.

## Variáveis Obrigatórias (Chatwoot)

Estas variáveis são **sempre necessárias**, independente de quais canais você está usando. Elas identificam qual instância do Chatwoot você está usando:

```bash
# URL do seu servidor Chatwoot (ex: https://chatwoot.seudominio.com)
CHATWOOT_BASE_URL=https://seu-chatwoot.com

# ID da conta no Chatwoot (encontre em Settings > Accounts)
CHATWOOT_ACCOUNT_ID=123

# Token de acesso da API (gere em Settings > Applications > Access Tokens)
CHATWOOT_API_ACCESS_TOKEN=seu_token_aqui
```

**Como o sistema identifica seu Chatwoot:**
- `CHATWOOT_BASE_URL` → Define qual servidor Chatwoot usar
- `CHATWOOT_ACCOUNT_ID` → Define qual conta dentro do servidor
- `CHATWOOT_API_ACCESS_TOKEN` → Autentica as requisições à API

## Variáveis Opcionais por Canal

### WhatsApp (Wasender)

Para habilitar o WhatsApp, configure todas estas variáveis:

```bash
WASENDER_WEBHOOK_ID=seu_webhook_id
WASENDER_WEBHOOK_SECRET=seu_secret
WASENDER_API_KEY=sua_api_key
WASENDER_INBOX_ID=123
CHATWOOT_WEBHOOK_ID_WHATSAPP=webhook_id_do_chatwoot
```

### Telegram

Para habilitar o Telegram, configure todas estas variáveis:

```bash
# Credenciais da API (https://my.telegram.org/apps)
TG_API_ID=12345678
TG_API_HASH=seu_api_hash
TG_INBOX_ID=123
CHATWOOT_WEBHOOK_ID_TELEGRAM=webhook_id_do_chatwoot

# Opcional: nome da sessão (default: session → arquivo session.session)
# TG_SESSION_NAME=session
```

**Sessão:** é preciso ter um arquivo `.session` (criado com login Telethon no seu PC). Se não declarar `TG_SESSION_NAME`, o app usa `session.session`. O ficheiro `session.session` pode ser commitado no repositório (os outros `*.session` continuam ignorados).

**Coolify (usar o ficheiro .session, sem base64):**
1. No Coolify, no teu serviço: **Persistent Storage** (ou **Volumes**) → adiciona um volume e monta em `/data` (ou outro caminho).
2. Coloca o ficheiro `session.session` dentro desse volume (por exemplo via SFTP, ou exec no container e upload do ficheiro).
3. Nas **Variáveis** do serviço, define: `TG_SESSION_PATH=/data/session.session` (ajusta o caminho se montaste noutro sítio).
4. O app usa esse ficheiro; não precisas de `TG_SESSION_BASE64`.

**Coolify (alternativa com base64):** se preferires não montar volume, podes usar `TG_SESSION_BASE64` com o base64 do `.session` (script: `py -3 scripts/export_session_base64.py`).

**Como funciona a integração:**
- `TG_INBOX_ID` → Define qual inbox do Chatwoot receberá as mensagens do Telegram
- `CHATWOOT_WEBHOOK_ID_TELEGRAM` → Identifica qual webhook receberá eventos do Chatwoot relacionados ao Telegram

### Como Descobrir TG_INBOX_ID e CHATWOOT_WEBHOOK_ID_TELEGRAM

#### 1. Encontrar o TG_INBOX_ID

O `TG_INBOX_ID` é o ID numérico do inbox do Telegram que você criou no Chatwoot.

**Passo a passo:**

1. Faça login no Chatwoot
2. Vá em **Settings** (Configurações) → **Inboxes** (Caixas de Entrada)
3. Procure pelo inbox do tipo **Telegram** (ou crie um novo se não existir)
4. Clique no inbox do Telegram
5. O ID do inbox aparece na URL do navegador ou você pode encontrá-lo de duas formas:
   - **Na URL**: Quando você está visualizando o inbox, a URL será algo como `https://seu-chatwoot.com/app/accounts/1/inboxes/123`, onde `123` é o `TG_INBOX_ID`
   - **Via API**: Faça uma requisição GET para `https://seu-chatwoot.com/api/v1/accounts/{ACCOUNT_ID}/inboxes` com seu token de acesso e procure pelo inbox do tipo "api" ou "telegram"

**Exemplo:**
Se a URL do inbox for `https://chatwoot.com/app/accounts/1/inboxes/42`, então `TG_INBOX_ID=42`

#### 2. Encontrar o CHATWOOT_WEBHOOK_ID_TELEGRAM

O `CHATWOOT_WEBHOOK_ID_TELEGRAM` é o ID do webhook que você cria no Chatwoot para receber eventos relacionados ao Telegram.

**Passo a passo:**

1. Faça login no Chatwoot
2. Vá em **Settings** (Configurações) → **Applications** (Aplicações) → **Webhooks**
3. Clique em **Add Webhook** (Adicionar Webhook)
4. Configure o webhook:
   - **URL**: `https://seu-dominio-coolify.com/chatwoot/webhook/{WEBHOOK_ID}`
     - Onde `{WEBHOOK_ID}` é um ID único que você escolhe (ex: `telegram-abc123`)
   - **Subscriptions**: Marque os eventos que deseja receber (pelo menos `message_created`)
5. Clique em **Create** (Criar)
6. Após criar, o Chatwoot mostrará o webhook criado. O **ID do webhook** é o valor que você usou em `{WEBHOOK_ID}` na URL, ou pode ser encontrado:
   - Na lista de webhooks (geralmente aparece como um identificador)
   - Na URL quando você edita o webhook
   - Via API: Faça uma requisição GET para `https://seu-chatwoot.com/api/v1/accounts/{ACCOUNT_ID}/webhooks` com seu token de acesso

**Exemplo:**
Se você criou o webhook com a URL `https://meu-app.com/chatwoot/webhook/telegram-xyz789`, então `CHATWOOT_WEBHOOK_ID_TELEGRAM=telegram-xyz789`

**Dica - Gerar um Webhook ID seguro:**
Você pode usar o script incluído no projeto para gerar um ID seguro:
```bash
poetry run gen-webhook-id
```
Ou execute diretamente:
```bash
python scripts/gen_webhook_id.py
```
Isso gerará um ID hexadecimal seguro de 64 caracteres que você pode usar como `CHATWOOT_WEBHOOK_ID_TELEGRAM`.

**Importante:**
- O `CHATWOOT_WEBHOOK_ID_TELEGRAM` deve ser o mesmo valor que você usou na URL do webhook no Chatwoot
- Esse ID é usado pelo sistema para identificar qual canal (telegram) está recebendo os eventos do Chatwoot
- Use um ID único e seguro para evitar conflitos

### VK (VKontakte)

Para habilitar o VK, configure todas estas variáveis:

```bash
VK_CALLBACK_ID=seu_callback_id_unico
VK_GROUP_ID=123456789
VK_ACCESS_TOKEN=seu_access_token
VK_SECRET=seu_secret_key
VK_CONFIRMATION=seu_confirmation_string
VK_API_VERSION=5.199
VK_INBOX_ID=123
CHATWOOT_WEBHOOK_ID_VK=webhook_id_do_chatwoot
```

**Nota:** O `VK_CONFIRMATION` é fornecido pelo VK quando você configura o Callback API.

## Resumo

- **Chatwoot**: Sempre obrigatório (3 variáveis) - identifica qual Chatwoot usar
- **WhatsApp**: Opcional, mas se usar, precisa de 5 variáveis
- **Telegram**: Opcional, mas se usar, precisa de 4 variáveis (TG_SESSION_NAME opcional; default: session)  
- **VK**: Opcional, mas se usar, precisa de 7 variáveis

## Exemplo: Configuração Mínima (Apenas Telegram)

```bash
# Chatwoot (obrigatório)
CHATWOOT_BASE_URL=https://seu-chatwoot.com
CHATWOOT_ACCOUNT_ID=123
CHATWOOT_API_ACCESS_TOKEN=seu_token_aqui

# Telegram (TG_SESSION_NAME opcional; default: session)
TG_API_ID=12345678
TG_API_HASH=seu_api_hash
TG_INBOX_ID=123
CHATWOOT_WEBHOOK_ID_TELEGRAM=webhook_id_do_chatwoot
```

## Como Configurar no Coolify

1. Vá para a seção de **Environment Variables** do seu aplicativo no Coolify
2. Adicione cada variável uma por uma
3. Certifique-se de que os valores estão corretos (sem espaços extras)
4. Reinicie o aplicativo após adicionar as variáveis

## Validação

A aplicação irá validar automaticamente as variáveis na inicialização. Se alguma variável obrigatória estiver faltando, a aplicação não iniciará e mostrará um erro indicando qual variável está faltando.
