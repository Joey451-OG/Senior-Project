"""Settings shared by main.py and the routers, all read from the environment (.env in
development, the service's environment file when deployed)."""

import logging
import os

logger = logging.getLogger("settings")


def readOrigins(variable_name: str) -> list[str]:
    """A comma-separated list of origins, e.g. "https://dash.example.com,http://localhost:3001"."""
    origins = []
    for origin in os.environ.get(variable_name, "").split(","):
        # Browsers send Origin without a trailing slash, so a pasted URL with one would never match
        origin = origin.strip().rstrip("/")
        if not origin:
            continue
        if not origin.startswith(("http://", "https://")):
            logger.error("%s: ignoring %r, an origin must start with http:// or https://", variable_name, origin)
            continue
        origins.append(origin)
    return origins


# Origins the dashboard page may be loaded from (scheme://host[:port], exactly as the browser shows
# it). The auth router refuses requests and WebSocket upgrades whose Origin header isn't listed, so
# another site can't ride on the session cookie. Empty means every browser request is refused.
allowed_origins = readOrigins("WARU_ALLOWED_ORIGINS")
if not allowed_origins:
    logger.error("WARU_ALLOWED_ORIGINS is not set; every browser request will be refused")

# Every Firebase ID token is checked against this project.
firebase_project_id = os.environ.get("FIREBASE_PROJECT_ID", "")

# The Firebase web-app config the frontend fetches from /auth/config before it starts. None of it is
# secret (Firebase web config is meant to ship to the browser); it lives here so one environment file
# configures both halves and the frontend never needs rebuilding for a different Firebase project.
firebase_web_config = {
    "api_key": os.environ.get("FIREBASE_API_KEY", ""),
    "auth_domain": os.environ.get("FIREBASE_AUTH_DOMAIN", ""),
    "project_id": firebase_project_id,
    "app_id": os.environ.get("FIREBASE_APP_ID", ""),
}
missing_firebase_variables = [
    variable_name for variable_name, value in (
        ("FIREBASE_API_KEY", firebase_web_config["api_key"]),
        ("FIREBASE_AUTH_DOMAIN", firebase_web_config["auth_domain"]),
        ("FIREBASE_PROJECT_ID", firebase_web_config["project_id"]),
        ("FIREBASE_APP_ID", firebase_web_config["app_id"]),
    ) if not value
]
if missing_firebase_variables:
    logger.error("Not set: %s; nobody will be able to sign in", ", ".join(missing_firebase_variables))

# Browsers drop Secure cookies over plain HTTP, so the dev script turns this off.
is_session_cookie_secure = os.environ.get("WARU_COOKIE_SECURE", "true").lower() != "false"

# The built frontend (`npm run build` output) that the API serves itself when installed as a service,
# so the reverse proxy in front of it needs only one upstream. start-backend.sh sets it; it stays
# unset in development, where Vite's dev server serves the frontend and proxies the API.
frontend_directory = os.environ.get("WARU_FRONTEND_DIR", "")
if frontend_directory and not os.path.isfile(os.path.join(frontend_directory, "index.html")):
    logger.error("WARU_FRONTEND_DIR=%s has no index.html; the dashboard page will not be served", frontend_directory)
    frontend_directory = ""
