#!/usr/bin/env bash
# 以 docker compose 建置並啟動網站與 worker；連接埠只綁定 127.0.0.1。停止：docker compose down
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v docker >/dev/null 2>&1; then
  echo "找不到 docker；請先安裝 Docker Desktop 或 OrbStack" >&2
  exit 1
fi
mkdir -p data
docker compose up -d --build --wait
docker compose ps
echo "網站：http://127.0.0.1"
