#!/bin/sh
# Starts the Tycheon API on loopback (synthetic data, a random per-boot key) and the public
# front door on the port Hugging Face routes to. Only the front door is reachable from outside.
set -eu

TYCHEON_DEMO_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"  # pragma: allowlist secret
export TYCHEON_DEMO_KEY
export TYCHEON_API_KEYS="demo:${TYCHEON_DEMO_KEY}"  # pragma: allowlist secret

tycheon-serve --host 127.0.0.1 --port 8080 --state-dir /tmp/state &

exec python -m uvicorn --app-dir /app proxy:create_app --factory \
    --host 0.0.0.0 --port 7860 --no-server-header --proxy-headers
