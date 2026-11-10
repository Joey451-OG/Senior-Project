"""Settings shared by main.py and the routers."""

import os

# The Vite dev server (port 3001) is a different origin from this API (port 3002), so the
# browser needs CORS headers before fetch/EventSource can read responses. The auth router
# also refuses requests and WebSocket upgrades sent from any other origin.
frontend_origins = [
    "http://130.74.96.14:3001",
    "http://jgsps.cs.olemiss.edu:3001",
    "http://localhost:3001",
]

# Every Firebase ID token is checked against this project.
firebase_project_id = os.environ.get("FIREBASE_PROJECT_ID", "")

# Browsers drop Secure cookies over plain HTTP, so the dev script turns this off.
is_session_cookie_secure = os.environ.get("WARU_COOKIE_SECURE", "true").lower() != "false"
