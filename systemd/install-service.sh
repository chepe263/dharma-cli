#!/usr/bin/env bash
# Instala el servicio systemd que arranca dharma-cli+KiroCrew (Docker) al boot.
# Rellena la ruta real del repo en el unit y lo habilita. Pide sudo.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_DIR="$(pwd)"
UNIT_SRC="systemd/dharma-cli.service"
UNIT_DST="/etc/systemd/system/dharma-cli.service"

if [ ! -f .env ]; then
  echo "ERROR: falta .env. Corre ./install.sh primero." >&2
  exit 1
fi

echo "==> Instalando ${UNIT_DST} (WorkingDirectory=${REPO_DIR}/docker)…"
sed "s|__REPO_DIR__|${REPO_DIR}|g" "${UNIT_SRC}" | sudo tee "${UNIT_DST}" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now dharma-cli.service

echo "==> Estado:"
systemctl --no-pager status dharma-cli.service | head -8 || true
cat <<EOF

Servicio instalado y habilitado (arranca al boot).
  Parar:     sudo systemctl stop dharma-cli
  Arrancar:  sudo systemctl start dharma-cli
  Logs:      (cd ${REPO_DIR}/docker && docker compose logs -f)
  Desinstalar: sudo systemctl disable --now dharma-cli && sudo rm ${UNIT_DST} && sudo systemctl daemon-reload
EOF
