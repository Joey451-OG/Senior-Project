import os
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import AuthAccounts
from routes import AuthRoute
from main import api

administrator_email = "admin@example.com"
allowed_origin = "http://localhost:3001"


# --- Fixtures ---

def makeClaims(email: str = administrator_email, has_second_factor: bool = True, email_verified: bool = True) -> dict:
    firebase_claims = {"sign_in_provider": "password"}
    if has_second_factor:
        firebase_claims["sign_in_second_factor"] = "totp"
    return {
        "uid": "uid-" + email, "email": email, "email_verified": email_verified,
        "exp": time.time() + 3600, "firebase": firebase_claims,
    }


@pytest.fixture
def fakeFirebase(monkeypatch):
    """Every token is treated as valid; the token text picks which claims it carries."""
    tokens = {
        "admin": makeClaims(),
        "newcomer": makeClaims(email="newcomer@example.com"),
        "no-mfa": makeClaims(has_second_factor=False),
        "unverified": makeClaims(email_verified=False),
    }

    def verifyIdToken(id_token, clock_skew_seconds=0):
        if id_token not in tokens:
            raise AuthRoute.auth.InvalidIdTokenError("bad token")
        return tokens[id_token]

    monkeypatch.setattr(AuthRoute, "firebase_project_id", "test-project")
    # The test client talks plain http, which drops Secure cookies (the dev script does the same).
    monkeypatch.setattr(AuthRoute, "is_session_cookie_secure", False)
    monkeypatch.setattr(AuthRoute.auth, "verify_id_token", verifyIdToken)
    monkeypatch.setattr(AuthRoute, "readAuthorizedEmails", lambda: {administrator_email})


@pytest.fixture
def client(fakeFirebase):
    with TestClient(api) as test_client:
        yield test_client


@pytest.fixture
def authAccountsFile(tmp_path, monkeypatch):
    """A temporary allow-list owned by the current user, who stands in for root."""
    file_path = tmp_path / "auth-accounts"
    file_path.write_text("")
    file_path.chmod(0o640)
    monkeypatch.setattr(AuthAccounts, "auth_accounts_file_path", str(file_path))
    monkeypatch.setattr(AuthAccounts, "root_uid", os.getuid())
    monkeypatch.setattr(AuthAccounts, "verifyAuthAccountsDirectory", lambda: None)
    return file_path


# --- Allow-list file ---

def test_AuthAccountsParsing(authAccountsFile):
    authAccountsFile.write_text("# admins\n\n  Admin@Example.com \nother@example.com\n#old@example.com\n")
    assert AuthAccounts.readAuthorizedEmails() == {"admin@example.com", "other@example.com"}


def test_AuthAccountsRefusesGroupWritableFile(authAccountsFile):
    authAccountsFile.chmod(0o660)
    with pytest.raises(AuthAccounts.AuthAccountsFileError, match="unsafe"):
        AuthAccounts.readAuthorizedEmails()


def test_AuthAccountsRefusesFileNotOwnedByRoot(authAccountsFile, monkeypatch):
    monkeypatch.setattr(AuthAccounts, "root_uid", os.getuid() + 1)
    with pytest.raises(AuthAccounts.AuthAccountsFileError, match="unsafe"):
        AuthAccounts.readAuthorizedEmails()


def test_AuthAccountsRefusesSymlink(authAccountsFile, tmp_path, monkeypatch):
    symlink_path = tmp_path / "auth-accounts-link"
    symlink_path.symlink_to(authAccountsFile)
    monkeypatch.setattr(AuthAccounts, "auth_accounts_file_path", str(symlink_path))
    with pytest.raises(AuthAccounts.AuthAccountsFileError, match="unsafe"):
        AuthAccounts.readAuthorizedEmails()


def test_AuthAccountsMissingFile(authAccountsFile):
    authAccountsFile.unlink()
    with pytest.raises(AuthAccounts.AuthAccountsFileError, match="not found"):
        AuthAccounts.readAuthorizedEmails()


def test_UnreadableAuthAccountsFileAuthorizesNobody(monkeypatch):
    def refuseFile():
        raise AuthAccounts.AuthAccountsFileError("Auth accounts file has unsafe ownership or permissions")
    monkeypatch.setattr(AuthRoute, "readAuthorizedEmails", refuseFile)
    assert AuthRoute.isAdministrator(makeClaims()) is False


# --- Sessions ---

def test_SessionForAdministrator(client):
    response = client.post("/auth/session", json={"id_token": "admin"})
    assert response.status_code == 200
    assert response.json() == {"email": administrator_email, "uid": "uid-" + administrator_email, "is_administrator": True}
    assert response.cookies.get(AuthRoute.session_cookie_name) == "admin"


def test_SessionForNewcomerIsNotAdministrator(client):
    client.post("/auth/session", json={"id_token": "newcomer"})
    response = client.get("/auth/me")
    assert response.status_code == 200
    assert response.json()["is_administrator"] is False


@pytest.mark.parametrize("token, detail", [("no-mfa", "Second factor required"), ("unverified", "Verified email required")])
def test_SessionRefusesIncompleteSignIn(client, token, detail):
    response = client.post("/auth/session", json={"id_token": token})
    assert response.status_code == 403
    assert response.json()["detail"] == detail


def test_SessionRefusesInvalidToken(client):
    assert client.post("/auth/session", json={"id_token": "forged"}).status_code == 401


def test_MeWithoutCookie(client):
    assert client.get("/auth/me").status_code == 401


def test_Logout(client):
    client.post("/auth/session", json={"id_token": "admin"})
    assert client.get("/auth/me").status_code == 200
    client.post("/auth/logout")
    assert client.get("/auth/me").status_code == 401


# --- Guarded routes ---

def test_PrivilegedRouteWithoutCookie(client):
    assert client.get("/papi/cron").status_code == 401


def test_PrivilegedRouteRefusesNewcomer(client):
    client.cookies.set(AuthRoute.session_cookie_name, "newcomer")
    response = client.get("/papi/cron")
    assert response.status_code == 403
    assert response.json()["detail"] == "Not an administrator"


def test_PrivilegedRouteRefusesTokenWithoutSecondFactor(client):
    client.cookies.set(AuthRoute.session_cookie_name, "no-mfa")
    assert client.get("/papi/cron").status_code == 403


def test_PrivilegedRouteAllowsAdministrator(client):
    client.cookies.set(AuthRoute.session_cookie_name, "admin")
    # Past auth, so whatever happens next is up to the cron helper (503 when it isn't running).
    assert client.get("/papi/cron").status_code not in (401, 403)


def test_PrivilegedRouteRefusesForeignOrigin(client):
    client.cookies.set(AuthRoute.session_cookie_name, "admin")
    response = client.get("/papi/cron", headers={"Origin": "http://evil.example.com"})
    assert response.status_code == 403


def test_WebSocketWithoutCookie(client):
    with pytest.raises(WebSocketDisconnect) as disconnect:
        with client.websocket_connect("/ws/cpu-load"):
            pass
    assert disconnect.value.code == 1008


def test_WebSocketRefusesForeignOrigin(client):
    client.cookies.set(AuthRoute.session_cookie_name, "admin")
    with pytest.raises(WebSocketDisconnect) as disconnect:
        with client.websocket_connect("/ws/cpu-load", headers={"Origin": "http://evil.example.com"}):
            pass
    assert disconnect.value.code == 1008


def test_WebSocket(client):
    client.cookies.set(AuthRoute.session_cookie_name, "admin")
    with client.websocket_connect("/ws/cpu-load", headers={"Origin": allowed_origin}) as websocket:
        data = websocket.receive_json()
        assert data["type"] == "cpu"
