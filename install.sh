#!/usr/bin/env bash
# Instalador portable de dharma-cli + KiroCrew (modo Docker) para una máquina nueva.
#
#   git clone <repo> && cd dharma-cli && ./install.sh
#
# Qué hace (idempotente — puedes correrlo varias veces):
#   1. Verifica Docker + compose.
#   2. Crea .env desde .env.example si no existe, y te pide la API key/modelo.
#   3. Detecta la IP del host y arma KIROCREW_CORS_ORIGINS (para abrirlo desde
#      otra máquina de la red).
#   4. Arranca el contenedor.
#   5. Te dice cómo sacar el token de login.
set -euo pipefail

cd "$(dirname "$0")"
REPO_DIR="$(pwd)"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
err() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; }

# ── 1. Docker ────────────────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
  err "Docker no está instalado. Instálalo primero: https://docs.docker.com/engine/install/"
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  err "El plugin 'docker compose' no está disponible. Instala docker-compose-plugin."
  exit 1
fi
say "Docker OK."

# ── 2. .env ──────────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  cp .env.example .env
  say "Creado .env desde .env.example."
  printf 'API key del endpoint (Ollama Cloud: https://ollama.com/settings/keys) [enter para editar luego]: '
  read -r API_KEY || true
  if [ -n "${API_KEY:-}" ]; then
    sed -i "s|^DHARMA_API_KEY=.*|DHARMA_API_KEY=${API_KEY}|" .env
  fi
  printf 'Modelo a usar [enter = gpt-oss:20b]: '
  read -r MODEL || true
  if [ -n "${MODEL:-}" ]; then
    sed -i "s|^DHARMA_MODEL=.*|DHARMA_MODEL=${MODEL}|" .env
  fi
else
  say ".env ya existe — lo respeto (edítalo a mano si hace falta)."
fi

# ── 3. IP del host + CORS + puerto ───────────────────────────────────────────
PORT="$(grep -E '^DHARMA_PORT=' .env 2>/dev/null | cut -d= -f2 || true)"
PORT="${PORT:-5477}"
HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
CORS="http://localhost:${PORT},http://127.0.0.1:${PORT}"
if [ -n "${HOST_IP:-}" ]; then
  CORS="http://${HOST_IP}:${PORT},${CORS}"
  say "IP del host detectada: ${HOST_IP} (podrás abrirlo desde la red en http://${HOST_IP}:${PORT})"
fi
# Exportar para docker compose (lo lee del entorno vía ${VAR} en el compose).
grep -q '^DHARMA_PORT=' .env || printf '\nDHARMA_PORT=%s\n' "$PORT" >> .env
if grep -q '^KIROCREW_CORS_ORIGINS=' .env; then
  sed -i "s|^KIROCREW_CORS_ORIGINS=.*|KIROCREW_CORS_ORIGINS=${CORS}|" .env
else
  printf 'KIROCREW_CORS_ORIGINS=%s\n' "$CORS" >> .env
fi

# ── 4. Arrancar ──────────────────────────────────────────────────────────────
say "Arrancando el contenedor…"
( cd docker && docker compose --env-file "${REPO_DIR}/.env" up -d )
sleep 6
if curl -fsS -o /dev/null "http://127.0.0.1:${PORT}/api/health"; then
  say "KiroCrew responde en http://127.0.0.1:${PORT} ✅"
else
  err "El contenedor arrancó pero /api/health no responde aún. Revisa: (cd docker && docker compose logs --tail 40)"
fi

# ── 5. Login ─────────────────────────────────────────────────────────────────
cat <<EOF

────────────────────────────────────────────────────────────
Instalación lista. Siguiente paso — sacar el token de login:

  ./login.sh

Luego abre el dashboard:
  http://127.0.0.1:${PORT}        (local)
$( [ -n "${HOST_IP:-}" ] && echo "  http://${HOST_IP}:${PORT}   (desde otra máquina de la red)" )

Para systemd (arrancar al boot, opcional):
  ./systemd/install-service.sh
────────────────────────────────────────────────────────────
EOF
