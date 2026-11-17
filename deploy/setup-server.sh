#!/usr/bin/env bash
# Prova Pronta — prepara um servidor Linux (Ubuntu) e coloca o site no ar com HTTPS.
# Uso, dentro da pasta do projeto clonado:   bash deploy/setup-server.sh
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
say() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }
ok() { printf '    \033[1;32mOK\033[0m  %s\n' "$1"; }

say "Instalando o Docker (se ainda não houver)"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER" || true
fi
ok "$(sudo docker --version)"

say "Liberando as portas 80 e 443 no firewall do servidor"
# Imagens Ubuntu da Oracle Cloud bloqueiam tudo além da porta 22 no iptables.
for port in 80 443; do
  if ! sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null; then
    sudo iptables -I INPUT 1 -p tcp --dport "$port" -j ACCEPT
  fi
done
if command -v netfilter-persistent >/dev/null 2>&1; then sudo netfilter-persistent save >/dev/null; fi
ok "Portas 80/443 abertas (lembre de liberá-las também na Security List da nuvem)"

say "Memória de troca (swap) para máquinas pequenas"
total_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$total_mb" -lt 3000 ] && [ ! -f /swapfile ]; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  ok "Swap de 2 GB criado"
else
  ok "Não é necessário (${total_mb} MB de RAM)"
fi

say "Configuração (.env)"
if [ ! -f .env ]; then
  cp .env.example .env
  ask() { local prompt="$2" secret="${3:-}" value; if [ -n "$secret" ]; then read -r -s -p "$prompt: " value; echo >&2; else read -r -p "$prompt: " value; fi; printf '%s' "$value"; }
  # Values may contain & or | (database URLs do), so lines are replaced without sed substitutions.
  set_env() { local key="$1" value="$2"; sed -i "/^$key=/d" .env; printf '%s=%s\n' "$key" "$value" >> .env; }
  set_env SITE_ADDRESS "$(ask SITE_ADDRESS 'Endereço do site (ex.: provamadre.duckdns.org)')"
  set_env ACCESS_CODE "$(ask ACCESS_CODE 'Código da escola para os professores entrarem')"
  set_env DATABASE_URL "$(ask DATABASE_URL 'DATABASE_URL do Neon (postgresql://...)' secret)"
  smtp_user="$(ask SMTP_USERNAME 'Gmail que envia os e-mails (deixe vazio para testar sem enviar)')"
  if [ -n "$smtp_user" ]; then
    set_env SMTP_USERNAME "$smtp_user"
    set_env SMTP_PASSWORD "$(ask SMTP_PASSWORD 'Senha de app do Gmail (16 letras)' secret)"
    set_env MAIL_MODE smtp
  fi
  chmod 600 .env
  ok ".env criado (somente este usuário pode ler)"
else
  ok ".env já existe (mantido)"
fi

say "Construindo e iniciando (a primeira vez leva alguns minutos)"
sudo docker compose --env-file .env -f deploy/docker-compose.yml up -d --build
site="$(grep '^SITE_ADDRESS=' .env | cut -d= -f2-)"
ok "Pronto! Abra https://${site}"
echo "    Para atualizar depois: bash deploy/update.sh"
