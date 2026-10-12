#!/bin/bash
# Container entrypoint: starts a virtual display (for adapters/nh_dhhs.py's
# headless=False requirement - see the Dockerfile comment), materializes
# secrets from Railway env vars into the local files the code expects
# (secrets.json, the Google service account key), then runs whatever
# command was passed in (normally `python3 run_all.py`).
set -e

# --- Virtual display ---
Xvfb :99 -screen 0 1280x1024x24 &
XVFB_PID=$!
export DISPLAY=:99
# Give Xvfb a moment to actually bind before Chromium tries to use it.
sleep 1

# --- Secrets from env vars, not local files ---
# secrets.json (41 portal credentials) and the Google service account key
# are gitignored and were never meant to live in the image - Railway
# supplies them as env vars (SECRETS_JSON / GOOGLE_SERVICE_ACCOUNT_JSON,
# each holding the raw file content), and this writes them out to the
# exact paths the code already expects, at container start, not build time.
if [ -n "$SECRETS_JSON" ]; then
    echo "$SECRETS_JSON" > /app/secrets.json
    chmod 600 /app/secrets.json
else
    echo "WARNING: SECRETS_JSON env var not set - portal logins will fail." >&2
fi

if [ -n "$GOOGLE_SERVICE_ACCOUNT_JSON" ]; then
    mkdir -p /app/.secrets
    echo "$GOOGLE_SERVICE_ACCOUNT_JSON" > /app/.secrets/service_account_key.json
    export SERVICE_ACCOUNT_KEY_PATH=/app/.secrets/service_account_key.json
else
    echo "WARNING: GOOGLE_SERVICE_ACCOUNT_JSON env var not set - Sheet writes will fail." >&2
fi

cleanup() {
    kill "$XVFB_PID" 2>/dev/null || true
}
trap cleanup EXIT

exec "$@"
