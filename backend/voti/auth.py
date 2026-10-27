"""Accounts: argon2 password hashing, opaque session tokens, FastAPI dependencies, login rate limit,
initial admin bootstrap.

Session cookie `voti_session` holds a random token; only its sha256 is stored (auth_sessions.token_hash),
so a database leak does not leak usable sessions.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta

import redis
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .api.errors import ApiError
from .config import get_config
from .db import get_session
from .models import AuthSession, User, utcnow

log = logging.getLogger("voti.auth")

COOKIE_NAME = "voti_session"
ROLES = ("admin", "annotator")
MIN_PASSWORD_LEN = 8

# Login rate limit: at most LOGIN_MAX_FAILURES failed attempts per IP per LOGIN_WINDOW_SEC.
LOGIN_MAX_FAILURES = 10
LOGIN_WINDOW_SEC = 15 * 60

_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash("voti-dummy-password-for-timing")


# ───────────────────────────── passwords ─────────────────────────────

def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def check_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise ApiError(400, "weak_password", f"密碼至少需要 {MIN_PASSWORD_LEN} 個字元")
    if len(password) > 1024:
        raise ApiError(400, "weak_password", "密碼太長")


# ───────────────────────────── sessions ─────────────────────────────

def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(s: Session, user: User, ip: str = "", user_agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    s.add(AuthSession(token_hash=token_hash(token), user_id=user.id,
                      expires_at=utcnow() + timedelta(days=get_config().session_days),
                      ip=ip[:64], user_agent=user_agent[:500]))
    # opportunistic cleanup
    s.execute(delete(AuthSession).where(AuthSession.expires_at < utcnow()))
    s.flush()
    return token


def revoke_session(s: Session, token: str) -> None:
    s.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(token)))


def revoke_user_sessions(s: Session, user_id, keep_token: str | None = None) -> None:  # noqa: ANN001
    q = delete(AuthSession).where(AuthSession.user_id == user_id)
    if keep_token:
        q = q.where(AuthSession.token_hash != token_hash(keep_token))
    s.execute(q)


def user_for_token(s: Session, token: str | None) -> User | None:
    if not token or len(token) > 200:
        return None
    row = s.execute(select(User, AuthSession).join(AuthSession, AuthSession.user_id == User.id)
                    .where(AuthSession.token_hash == token_hash(token))).first()
    if row is None:
        return None
    user, sess = row
    if sess.expires_at <= utcnow() or not user.is_active:
        return None
    return user


def cookie_secure(request: Request) -> bool:
    """Secure flag when the browser-facing connection is https (uvicorn --proxy-headers rewrites the scheme;
    the raw header is checked too in case the app runs without that flag)."""
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or proto == "https"


def set_session_cookie(request: Request, response, token: str) -> None:  # noqa: ANN001
    response.set_cookie(COOKIE_NAME, token, max_age=get_config().session_days * 86400, httponly=True,
                        samesite="lax", secure=cookie_secure(request), path="/")


def clear_session_cookie(request: Request, response) -> None:  # noqa: ANN001
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax", secure=cookie_secure(request))


# ───────────────────────────── dependencies ─────────────────────────────

def current_user(request: Request, s: Session = Depends(get_session)) -> User:
    user = user_for_token(s, request.cookies.get(COOKIE_NAME))
    if user is None:
        raise ApiError(401, "not_authenticated", "尚未登入或登入已過期")
    request.state.user = user
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise ApiError(403, "admin_required", "需要管理員權限")
    return user


# ───────────────────────────── login rate limit ─────────────────────────────

def _rl_key(ip: str) -> str:
    return f"voti:login_fail:{ip}"


def _redis() -> redis.Redis:
    from .queue import redis_client
    return redis_client()


def check_login_rate(ip: str) -> None:
    try:
        n = _redis().get(_rl_key(ip))
    except redis.RedisError:
        log.warning("redis unavailable; login rate limit not enforced")
        return
    if n is not None and int(n) >= LOGIN_MAX_FAILURES:
        raise ApiError(429, "rate_limited", "登入失敗次數過多，請 15 分鐘後再試",
                       headers={"Retry-After": str(LOGIN_WINDOW_SEC)})


def record_login_failure(ip: str) -> None:
    try:
        r = _redis()
        pipe = r.pipeline()
        pipe.incr(_rl_key(ip))
        pipe.expire(_rl_key(ip), LOGIN_WINDOW_SEC, nx=True)
        pipe.execute()
    except redis.RedisError:
        pass


def clear_login_failures(ip: str) -> None:
    try:
        _redis().delete(_rl_key(ip))
    except redis.RedisError:
        pass


def authenticate(s: Session, username: str, password: str) -> User | None:
    user = s.scalar(select(User).where(User.username == username))
    if user is None:
        verify_password(_DUMMY_HASH, password)  # equalize timing
        return None
    if not verify_password(user.password_hash, password) or not user.is_active:
        return None
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return user


# ───────────────────────────── bootstrap ─────────────────────────────

def bootstrap_admin(s: Session) -> User | None:
    """Create ADMIN_USERNAME/ADMIN_PASSWORD when no user exists yet. Never touches existing accounts."""
    if s.scalar(select(func.count()).select_from(User)):
        return None
    cfg = get_config()
    if not cfg.admin_password:
        log.error("=" * 70)
        log.error("NO USERS EXIST and ADMIN_PASSWORD is empty: no admin account was created.")
        log.error("Set ADMIN_PASSWORD in .env and restart, or run: python -m voti.cli create-user")
        log.error("=" * 70)
        return None
    if len(cfg.admin_password) < MIN_PASSWORD_LEN:
        log.error("ADMIN_PASSWORD is shorter than %d characters; refusing to create the initial admin.",
                  MIN_PASSWORD_LEN)
        return None
    s.execute(select(func.pg_advisory_xact_lock(func.hashtext("voti.bootstrap_admin"))))
    if s.scalar(select(func.count()).select_from(User)):  # another process won the race
        return None
    user = User(username=cfg.admin_username, display_name=cfg.admin_username,
                password_hash=hash_password(cfg.admin_password), role="admin", is_active=True)
    s.add(user)
    s.flush()
    log.warning("created initial admin account %r", cfg.admin_username)
    return user
