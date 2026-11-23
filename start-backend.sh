#!/bin/bash
# Runs the dashboard API for waru-api.service, as the unprivileged `waru` service account. systemd
# supplies the environment from /etc/waru/waru.env. For development use run-backend-dev.sh instead.
set -euo pipefail

# The directory this script was installed into (/opt/waru), so nothing below hardcodes it.
install_dir=$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")

# The API serves the built frontend itself, so the reverse proxy forwards everything to one port.
export WARU_FRONTEND_DIR="${WARU_FRONTEND_DIR:-$install_dir/frontend}"
port="${WARU_PORT:-3002}"

# Only the reverse proxy on this machine talks to the API, so it listens on loopback only and trusts
# X-Forwarded-* headers from loopback only. Exactly one worker process: the scheduler, the change
# version counters and the WebSocket/SSE client lists all live in memory in this one process.
exec "$install_dir/venv/bin/uvicorn" main:api \
    --app-dir "$install_dir/backend" \
    --host 127.0.0.1 --port "$port" \
    --proxy-headers --forwarded-allow-ips 127.0.0.1 \
    --no-server-header
