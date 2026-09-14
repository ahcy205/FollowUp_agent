#!/bin/sh
set -e
# Apply database migrations, then serve. Railway provides $PORT and terminates HTTPS at its edge.
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips="*" --workers 1
