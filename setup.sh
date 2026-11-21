#!/usr/bin/env bash
# Runs once when the Codespace is created: installs packages, starts PostgreSQL, applies migrations.
set -euo pipefail
cd "$(dirname "$0")/../backend"
pip install --quiet -e ".[dev]"
python -m scripts.init_env
docker compose -f ../docker-compose.yml up -d --wait db mailpit
python -m scripts.bootstrap_db
alembic upgrade head
echo "StockFlow setup complete."
