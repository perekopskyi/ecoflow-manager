#!/usr/bin/env bash
# Run on your Mac from the bot folder:  ./deploy.sh
# Copies the folder to the Debian server and (re)builds the Docker container there.
set -euo pipefail

SERVER="yevhenii@192.168.0.61"   # <-- EDIT
REMOTE_DIR="ecoflow"                        # relative to the home dir on the server

if [ ! -f requirements.txt ]; then
  echo "requirements.txt not found. Create it first (e.g. 'pip freeze > requirements.txt' inside .venv)."
  exit 1
fi
if [ ! -f .env ]; then
  echo ".env not found. Copy .env.example to .env and fill it in."
  exit 1
fi

echo "==> Copying files to $SERVER:~/$REMOTE_DIR"
ssh "$SERVER" "mkdir -p ~/$REMOTE_DIR"
rsync -avz --delete \
  --exclude '.venv' --exclude '__pycache__' --exclude '*.pyc' \
  --exclude '.git' --exclude 'quota_all.json' \
  ./ "$SERVER:~/$REMOTE_DIR/"

echo "==> Securing .env and starting the container"
ssh "$SERVER" "cd ~/$REMOTE_DIR && chmod 600 .env && docker compose up -d --build && docker compose ps"

echo "==> Done. Logs: ssh $SERVER 'cd ~/$REMOTE_DIR && docker compose logs -f'"
