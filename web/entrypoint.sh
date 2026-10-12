#!/bin/bash
# Container entrypoint: materializes the Google service account key from a
# Railway env var into the path core/sheets.py expects (same pattern as the
# scraper's own entrypoint.sh), then starts the dashboard server.
set -e

if [ -n "$GOOGLE_SERVICE_ACCOUNT_JSON" ]; then
    mkdir -p /app/.secrets
    echo "$GOOGLE_SERVICE_ACCOUNT_JSON" > /app/.secrets/service_account_key.json
    export SERVICE_ACCOUNT_KEY_PATH=/app/.secrets/service_account_key.json
else
    echo "WARNING: GOOGLE_SERVICE_ACCOUNT_JSON env var not set - Sheet reads will fail." >&2
fi

exec uvicorn app:app --host 0.0.0.0 --port "${PORT:-8000}"
