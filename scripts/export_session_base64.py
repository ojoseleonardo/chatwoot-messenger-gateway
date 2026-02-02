"""
Lê o ficheiro .session e imprime o base64 para colar em TG_SESSION_BASE64 no Coolify.
Executa na raiz do projeto (onde está o .session).

Uso:
  py -3 scripts/export_session_base64.py

Copia a saída e cola no Coolify em Variáveis → TG_SESSION_BASE64
"""
import base64
import os
import sys

try:
    from dotenv import load_dotenv
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(root, ".env"))
except ImportError:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

session_name = (os.getenv("TG_SESSION_NAME") or "session").strip()
session_file = os.path.join(root, f"{session_name}.session")

if not os.path.isfile(session_file):
    print(f"Ficheiro não encontrado: {session_file}", file=sys.stderr)
    print("Corre primeiro: py -3 scripts/login_telegram.py", file=sys.stderr)
    sys.exit(1)

with open(session_file, "rb") as f:
    b64 = base64.b64encode(f.read()).decode("ascii")

print("Copia o bloco abaixo e cola em Coolify → Variáveis → TG_SESSION_BASE64:")
print()
print(b64)
print()
