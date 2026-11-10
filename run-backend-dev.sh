#!/bin/bash

source /home/jgadmin/Senior-Project/.venv/bin/activate

# FIREBASE_PROJECT_ID (and anything else the API reads) lives in the gitignored .env
if [ -f /home/jgadmin/Senior-Project/.env ]; then
    set -a
    source /home/jgadmin/Senior-Project/.env
    set +a
fi
# The dev servers are plain HTTP, where browsers drop Secure cookies
export WARU_COOKIE_SECURE=false

fastapi dev /home/jgadmin/Senior-Project/src/backend/main.py --host 0.0.0.0 --port 3002
