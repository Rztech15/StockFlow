#!/usr/bin/env bash
# Starts the API every time the Codespace is opened (keep this terminal open).
cd "$(dirname "$0")/../backend"
docker compose -f ../docker-compose.yml up -d --wait db mailpit
exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
