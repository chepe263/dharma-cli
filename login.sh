#!/usr/bin/env bash
# login.sh — mintea un token del dashboard del contenedor y te imprime el link
# YA armado con la IP del host y el puerto publicado, para no hacer a mano el
# baile de "copiar el link, cambiar localhost por la IP y el puerto".
#
# Uso:
#   ./login.sh                 # contenedor por defecto: kirocrew-faux
#   ./login.sh <contenedor>    # otro nombre
#   TTL=4h ./login.sh          # otra vigencia (default 2h)
#
# Lo corres TÚ: llama a `kirocrew token` dentro del contenedor (mint de
# credencial), que es una acción del operador.
set -euo pipefail

CONTAINER="${1:-kirocrew-faux}"
TTL="${TTL:-2h}"

# 1. Puerto del host que mapea al 5476 del contenedor (ej. "0.0.0.0:5477" -> 5477).
PORT="$(docker port "$CONTAINER" 5476/tcp 2>/dev/null | head -1 | sed 's/.*://')"
if [ -z "${PORT:-}" ]; then
  echo "No pude leer el puerto publicado de '$CONTAINER'. ¿Está corriendo? (docker compose up -d)" >&2
  exit 1
fi

# 2. IP LAN del host (la que otra máquina usaría para llegar aquí).
HOST_IP="$(ip route get 1.1.1.1 2>/dev/null | grep -oE 'src [0-9.]+' | awk '{print $2}' | head -1)"
[ -z "${HOST_IP:-}" ] && HOST_IP="localhost"

# 3. Mintea el token dentro del contenedor.
RAW="$(docker exec "$CONTAINER" kirocrew token --ttl "$TTL" 2>&1)"
TOKEN="$(printf '%s\n' "$RAW" | grep -oE 'token=[A-Za-z0-9._-]+' | head -1 | cut -d= -f2)"
if [ -z "${TOKEN:-}" ]; then
  echo "No pude extraer el token. Salida cruda:" >&2
  printf '%s\n' "$RAW" >&2
  exit 1
fi

# 4. Arma el link con host:puerto correctos y ofrece ambas variantes.
echo
echo "Dashboard del Kiro Crew (faux backend) listo. Abre en tu navegador:"
echo
echo "  Desde ESTA máquina:   http://localhost:${PORT}?token=${TOKEN}"
echo "  Desde OTRA máquina:   http://${HOST_IP}:${PORT}?token=${TOKEN}"
echo
echo "(Si Chrome rechaza el origen, añade esa IP:puerto a KIROCREW_CORS_ORIGINS en docker/compose.yaml)"
