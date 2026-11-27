#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Install Docker Engine and the Docker Compose plugin first."
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin is missing. Install it before continuing."
  exit 1
fi
if [ ! -f .env ]; then
  echo "First-time setup: create private local credentials."
  read -r -s -p "Choose a teacher password (at least 12 characters): " password
  echo
  if [ "${#password}" -lt 12 ]; then echo "Password is too short."; exit 1; fi
  secret="$(openssl rand -base64 48 | tr -d '\n')"
  printf 'EXAMEVAL_ADMIN_PASSWORD=%s\nEXAMEVAL_TOKEN_SECRET=%s\n' "$password" "$secret" > .env
  chmod 600 .env
  unset password secret
  echo "Saved private credentials in .env."
fi
docker compose up -d --build
echo "ExamEval is starting at http://localhost:8000"
