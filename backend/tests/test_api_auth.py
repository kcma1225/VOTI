"""Auth, sessions, bootstrap, host + same-origin protection, users admin."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from conftest import ORIGIN, TEST_PASSWORD


def test_healthz_public(client):
    r = client.get("/api/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_requires_login(client):
    for path in ("/api/auth/me", "/api/media", "/api/segments", "/api/jobs", "/api/stats/overview"):
        r = client.get(path)
        assert r.status_code == 401, path
        assert r.json()["code"] == "not_authenticated"


def test_login_logout_me(client, make_user, db):
    from voti.models import AuthSession
    make_user("alice")
    r = client.post("/api/auth/login", json={"username": "alice", "password": "wrong password"})
    assert r.status_code == 401 and r.json()["code"] == "invalid_credentials"
    r = client.post("/api/auth/login", json={"username": "alice", "password": TEST_PASSWORD})
    assert r.status_code == 200
    assert r.json()["username"] == "alice" and r.json()["role"] == "annotator"
    cookie = r.headers["set-cookie"]
    assert "voti_session=" in cookie and "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    assert "Secure" not in cookie
    token = client.cookies.get("voti_session")
    stored = db.scalars(select(AuthSession.token_hash)).all()
    assert len(stored) == 1 and stored[0] != token and len(stored[0]) == 64  # only the hash is stored
    assert client.get("/api/auth/me").json()["username"] == "alice"
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_secure_cookie_behind_https_proxy(client, make_user):
    make_user("bob")
    r = client.post("/api/auth/login", json={"username": "bob", "password": TEST_PASSWORD},
                    headers={"X-Forwarded-Proto": "https"})
    assert r.status_code == 200
    assert "Secure" in r.headers["set-cookie"]


def test_inactive_user_cannot_login(client, make_user):
    make_user("carol", active=False)
    r = client.post("/api/auth/login", json={"username": "carol", "password": TEST_PASSWORD})
    assert r.status_code == 401


def test_login_rate_limit(client, make_user):
    from voti.auth import LOGIN_MAX_FAILURES
    make_user("dave")
    for _ in range(LOGIN_MAX_FAILURES):
        assert client.post("/api/auth/login", json={"username": "dave", "password": "nope-nope"}).status_code == 401
    r = client.post("/api/auth/login", json={"username": "dave", "password": TEST_PASSWORD})
    assert r.status_code == 429 and r.json()["code"] == "rate_limited"


def test_password_change(client, make_user):
    make_user("erin")
    client.post("/api/auth/login", json={"username": "erin", "password": TEST_PASSWORD})
    r = client.post("/api/auth/password", json={"current_password": "bad", "new_password": "new password 1"})
    assert r.status_code == 400
    r = client.post("/api/auth/password", json={"current_password": TEST_PASSWORD, "new_password": "short"})
    assert r.status_code == 400 and r.json()["code"] == "weak_password"
    r = client.post("/api/auth/password", json={"current_password": TEST_PASSWORD, "new_password": "new password 1"})
    assert r.status_code == 204
    assert client.get("/api/auth/me").status_code == 200  # current session kept
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"username": "erin", "password": "new password 1"}).status_code == 200


# ───────────────────────────── bootstrap ─────────────────────────────

def test_bootstrap_admin(db, monkeypatch):
    from voti.auth import bootstrap_admin, verify_password
    from voti.config import get_config
    from voti.models import User

    # empty password: refuses
    monkeypatch.setattr(get_config(), "admin_password", "")
    assert bootstrap_admin(db) is None
    assert db.scalar(select(func.count()).select_from(User)) == 0
    # configured: creates the admin once
    monkeypatch.setattr(get_config(), "admin_password", "bootstrap-secret")
    u = bootstrap_admin(db)
    db.commit()
    assert u is not None and u.role == "admin" and u.username == "admin"
    assert verify_password(u.password_hash, "bootstrap-secret")
    # never re-applied once a user exists (even with a different password)
    monkeypatch.setattr(get_config(), "admin_password", "another-password")
    assert bootstrap_admin(db) is None
    db.refresh(u)
    assert verify_password(u.password_hash, "bootstrap-secret")
    assert db.scalar(select(func.count()).select_from(User)) == 1


def test_startup_bootstraps(db, monkeypatch):
    from voti.api import app as app_module
    from voti.config import get_config
    from voti.models import User

    monkeypatch.setattr(get_config(), "admin_password", "startup-secret")
    with TestClient(app_module.create_app(), base_url=ORIGIN, headers={"Origin": ORIGIN}) as c:
        assert c.get("/api/healthz").status_code == 200
        r = c.post("/api/auth/login", json={"username": "admin", "password": "startup-secret"})
        assert r.status_code == 200 and r.json()["role"] == "admin"
    assert db.scalar(select(func.count()).select_from(User)) == 1


# ───────────────────────────── host / same-origin ─────────────────────────────

def test_host_allowlist(app):
    ok = TestClient(app, base_url="http://voti.example.test")
    assert ok.get("/api/healthz").status_code == 200
    assert TestClient(app, base_url="http://127.0.0.1:8080").get("/api/healthz").status_code == 200
    bad = TestClient(app, base_url="http://evil.example.com")
    r = bad.get("/api/healthz")
    assert r.status_code == 400 and r.json()["code"] == "invalid_host"


def test_same_origin_required_for_writes(app, make_user):
    make_user("frank")
    body = {"username": "frank", "password": TEST_PASSWORD}
    c = TestClient(app, base_url=ORIGIN)
    # no Origin and no Sec-Fetch-Site: refused
    r = c.post("/api/auth/login", json=body)
    assert r.status_code == 403 and r.json()["code"] == "cross_site"
    # foreign origin
    assert c.post("/api/auth/login", json=body, headers={"Origin": "https://evil.example.com"}).status_code == 403
    assert c.post("/api/auth/login", json=body, headers={"Origin": "null"}).status_code == 403
    # Sec-Fetch-Site is authoritative
    assert c.post("/api/auth/login", json=body, headers={"Sec-Fetch-Site": "cross-site",
                                                         "Origin": ORIGIN}).status_code == 403
    assert c.post("/api/auth/login", json=body, headers={"Sec-Fetch-Site": "same-site"}).status_code == 403
    assert c.post("/api/auth/login", json=body, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    # matching Origin
    assert c.post("/api/auth/login", json=body, headers={"Origin": ORIGIN}).status_code == 200
    # GET is not checked
    assert c.get("/api/auth/me", headers={"Origin": "https://evil.example.com"}).status_code == 200


def test_security_headers(client):
    r = client.get("/api/healthz")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


# ───────────────────────────── users (admin) ─────────────────────────────

def test_users_admin_only(annotator_client):
    assert annotator_client.get("/api/users").status_code == 403
    r = annotator_client.post("/api/users", json={"username": "x1", "password": "password123"})
    assert r.status_code == 403 and r.json()["code"] == "admin_required"
    assert annotator_client.get("/api/settings").status_code == 403


def test_users_crud_and_audit(admin_client, db):
    from voti.models import AuditLog
    r = admin_client.post("/api/users", json={"username": "gina", "display_name": "Gina", "password": "password123",
                                              "role": "annotator"})
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    assert admin_client.post("/api/users", json={"username": "gina", "password": "password123"}).status_code == 409
    assert admin_client.post("/api/users", json={"username": "h", "password": "x"}).status_code == 422
    r = admin_client.patch(f"/api/users/{uid}", json={"role": "admin", "display_name": "G."})
    assert r.status_code == 200 and r.json()["role"] == "admin"
    r = admin_client.get("/api/users")
    assert r.json()["total"] == 2
    actions = db.scalars(select(AuditLog.action).order_by(AuditLog.id)).all()
    assert actions == ["user.create", "user.update"]
    row = db.scalar(select(AuditLog).where(AuditLog.action == "user.update"))
    assert row.before["role"] == "annotator" and row.after["role"] == "admin"


def test_cannot_remove_last_admin(admin_client, admin_user):
    r = admin_client.patch(f"/api/users/{admin_user.id}", json={"role": "annotator"})
    assert r.status_code == 409 and r.json()["code"] == "last_admin"


def test_deactivated_user_session_revoked(app, admin_client, make_user):
    from conftest import _login
    u = make_user("henry")
    c = _login(app, "henry")
    assert c.get("/api/auth/me").status_code == 200
    assert admin_client.patch(f"/api/users/{u.id}", json={"is_active": False}).status_code == 200
    assert c.get("/api/auth/me").status_code == 401


def test_cli_create_and_reset(db, monkeypatch, capsys):
    from voti import cli
    from voti.auth import verify_password
    from voti.models import User

    monkeypatch.setenv("VOTI_PASSWORD", "cli-password-1")
    assert cli.main(["create-user", "ivan", "--role", "admin"]) == 0
    u = db.scalar(select(User).where(User.username == "ivan"))
    assert u.role == "admin" and verify_password(u.password_hash, "cli-password-1")
    monkeypatch.setenv("VOTI_PASSWORD", "cli-password-2")
    assert cli.main(["reset-password", "ivan"]) == 0
    db.expire_all()
    assert verify_password(db.get(User, u.id).password_hash, "cli-password-2")
    assert cli.main(["init"]) == 0  # users exist → not re-applied
    assert "not (re)applied" in capsys.readouterr().out
