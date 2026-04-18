#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "[1/4] Checking required files..."
for f in .env config.json docker-compose.yml Dockerfile requirements.txt; do
  if [[ ! -f "$f" ]]; then
    echo "Missing required file: $f"
    exit 1
  fi
done

echo "[2/4] Ensuring data directory exists..."
mkdir -p data/markets

echo "[3/4] Building image..."
docker compose build --pull

echo "[4/4] Starting service..."
docker compose up -d

echo "Done. Current status:"
docker compose ps

echo "Recent logs:"
docker compose logs --tail=50
