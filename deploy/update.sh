#!/usr/bin/env bash
# Atualiza o Prova Pronta no servidor com a versão mais recente do GitHub.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
sudo docker compose --env-file .env -f deploy/docker-compose.yml up -d --build
sudo docker image prune -f >/dev/null
echo "Atualizado."
