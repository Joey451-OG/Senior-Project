"""
Sign-in for the dashboard, backed by Firebase Authentication.

The browser signs in with Firebase (email or Google, plus a TOTP second
factor) and posts the resulting ID token to /auth/session. Once verified, the token
itself is kept in an HttpOnly cookie, because browsers can't attach headers to
WebSocket upgrades or EventSource requests but do send cookies with both. Firebase
refreshes ID tokens about once an hour, and the frontend posts each new one.

requireAdministrator is the one place that decides who may use the dashboard; the
/papi and /ws routers depend on it as a whole, so swapping the auth scheme later only
touches this file. A user passes only when their token:
    - is a valid Firebase ID token for this project
    - was issued after a second-factor sign-in (Firebase can't force enrollment itself)
    - carries a verified email that is listed in /etc/waru/auth-accounts
"""

import logging
import time
from typing import Optional

import firebase_admin
import google.auth.credentials
from fastapi import APIRouter, Depends, HTTPException, Response, WebSocketException, status
from firebase_admin import auth, credentials
from starlette.requests import HTTPConnection

from AuthAccounts import AuthAccountsFileError, readAuthorizedEmails
from PROJECTTypes import AuthStatus, SessionRequest
from ServerSettings import firebase_project_id, frontend_origins, is_session_cookie_secure

logger = logging.getLogger("auth")

session_cookie_name = "waru_session"
# Tolerates small clock differences between this machine and Google's.
token_clock_skew_seconds = 10


class AnonymousCredential(credentials.Base):
    """
    Verifying ID tokens only needs Google's public certificates, but firebase_admin
    insists on a credential and would otherwise go looking for a service account.
    """

    def get_credential(self):
        return google.auth.credentials.AnonymousCredentials()


if firebase_project_id:
    firebase_admin.initialize_app(AnonymousCredential(), options={"projectId": firebase_project_id})
else:
    logger.error("FIREBASE_PROJECT_ID is not set; every sign-in will be refused")


class AuthFailure(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# --- Checks ---

def checkOrigin(connection: HTTPConnection) -> None:
    """
    Browsers always send Origin on WebSocket upgrades and cross-origin requests, so a
    foreign one means another site is trying to ride on the session cookie. Clients
    that send none (curl, websocat) aren't browsers and so don't hold the cookie.
    """
    origin = connection.headers.get("origin")
    if origin is not None and origin not in frontend_origins:
        raise AuthFailure(status.HTTP_403_FORBIDDEN, "Origin not allowed")


def verifyFirebaseToken(id_token: Optional[str]) -> dict:
    if not id_token:
        raise AuthFailure(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    if not firebase_project_id:
        raise AuthFailure(status.HTTP_503_SERVICE_UNAVAILABLE, "Authentication is not configured")

    try:
        claims = auth.verify_id_token(id_token, clock_skew_seconds=token_clock_skew_seconds)
    except auth.ExpiredIdTokenError:
        raise AuthFailure(status.HTTP_401_UNAUTHORIZED, "Session expired") from None
    except (auth.InvalidIdTokenError, ValueError):
        raise AuthFailure(status.HTTP_401_UNAUTHORIZED, "Invalid session") from None
    except auth.CertificateFetchError as error:
        logger.warning("Cannot fetch Firebase certificates: %s", error)
        raise AuthFailure(status.HTTP_503_SERVICE_UNAVAILABLE, "Cannot reach Firebase to verify the session") from None

    hasSecondFactor = bool(claims.get("firebase", {}).get("sign_in_second_factor"))
    if not hasSecondFactor:
        raise AuthFailure(status.HTTP_403_FORBIDDEN, "Second factor required")
    if not claims.get("email") or not claims.get("email_verified"):
        raise AuthFailure(status.HTTP_403_FORBIDDEN, "Verified email required")
    return claims


def isAdministrator(claims: dict) -> bool:
    """Fails closed: an allow-list that can't be trusted authorizes nobody."""
    try:
        authorized_emails = readAuthorizedEmails()
    except AuthAccountsFileError as error:
        logger.warning("Nobody is authorized: %s", error)
        return False
    return claims["email"].lower() in authorized_emails


def buildAuthStatus(claims: dict) -> AuthStatus:
    return AuthStatus(email=claims["email"], uid=claims["uid"], is_administrator=isAdministrator(claims))


def raiseForAuthFailure(connection: HTTPConnection, failure: AuthFailure) -> None:
    if connection.scope["type"] == "websocket":
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason=failure.detail)
    raise HTTPException(status_code=failure.status_code, detail=failure.detail)


# --- Dependencies ---

def requireAllowedOrigin(connection: HTTPConnection) -> None:
    try:
        checkOrigin(connection)
    except AuthFailure as failure:
        raiseForAuthFailure(connection, failure)


def requireAdministrator(connection: HTTPConnection) -> dict:
    """
    Guards every /papi and /ws route. Works for HTTP requests and WebSocket upgrades
    alike; a refused WebSocket is closed with 1008 (policy violation). Sync on purpose:
    FastAPI runs it in a thread, since a token check may have to fetch certificates.
    """
    try:
        checkOrigin(connection)
        claims = verifyFirebaseToken(connection.cookies.get(session_cookie_name))
        if not isAdministrator(claims):
            raise AuthFailure(status.HTTP_403_FORBIDDEN, "Not an administrator")
    except AuthFailure as failure:
        raiseForAuthFailure(connection, failure)
    return claims


# --- Routes (final paths: /auth/session, /auth/me, /auth/logout) ---

auth_router = APIRouter(prefix="/auth", tags=["Authentication"], dependencies=[Depends(requireAllowedOrigin)])


@auth_router.post("/session", response_model=AuthStatus)
def createSession(session_request: SessionRequest, response: Response) -> AuthStatus:
    """
    Verifies a fresh Firebase ID token and stores it in the session cookie. Signed-in
    users who aren't administrators still get a session, so the frontend can show them
    the "not yet an administrator" page and notice when they are added.
    """
    try:
        claims = verifyFirebaseToken(session_request.id_token)
    except AuthFailure as failure:
        raise HTTPException(status_code=failure.status_code, detail=failure.detail) from None

    response.set_cookie(
        session_cookie_name, session_request.id_token,
        max_age=max(int(claims["exp"] - time.time()), 0),
        httponly=True, secure=is_session_cookie_secure, samesite="strict",
    )
    return buildAuthStatus(claims)


@auth_router.get("/me", response_model=AuthStatus)
def getAuthStatus(connection: HTTPConnection) -> AuthStatus:
    try:
        claims = verifyFirebaseToken(connection.cookies.get(session_cookie_name))
    except AuthFailure as failure:
        raise HTTPException(status_code=failure.status_code, detail=failure.detail) from None
    return buildAuthStatus(claims)


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def deleteSession(response: Response) -> None:
    response.delete_cookie(session_cookie_name, httponly=True, secure=is_session_cookie_secure, samesite="strict")
