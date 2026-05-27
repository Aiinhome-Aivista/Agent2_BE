#!/usr/bin/env bash
# Convenience launcher — runs the FastAPI backend with uvicorn.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "→ .env not found, copying from .env.example"
  cp .env.example .env
fi

if [ -f "./venv/bin/uvicorn" ]; then
  exec ./venv/bin/uvicorn app.main:app --host localhost --port 8000 --reload
else
  exec uvicorn app.main:app --host localhost --port 8000 --reload
fi
