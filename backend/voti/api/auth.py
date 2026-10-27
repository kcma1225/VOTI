"""/api/auth/*: login, logout, me, password change."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from .. import auth as A
from ..db import get_session
from ..models import User, utcnow
from . import schemas as S
from .errors import ApiError
from .views import audit, user_out

router = APIRouter(prefix="/auth", tags=["auth"])


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


@router.post("/login", response_model=S.UserOut)
def login(body: S.LoginIn, request: Request, response: Response, s: Session = Depends(get_session)):
    ip = client_ip(request)
    A.check_login_rate(ip)
    user = A.authenticate(s, body.username.strip(), body.password)
    if user is None:
        A.record_login_failure(ip)
        raise ApiError(401, "invalid_credentials", "帳號或密碼錯誤")
    A.clear_login_failures(ip)
    user.last_login_at = utcnow()
    token = A.create_session(s, user, ip=ip, user_agent=request.headers.get("user-agent", ""))
    s.commit()
    A.set_session_cookie(request, response, token)
    return user_out(user)


@router.post("/logout", status_code=204, response_class=Response)
def logout(request: Request, s: Session = Depends(get_session)):
    token = request.cookies.get(A.COOKIE_NAME)
    if token:
        A.revoke_session(s, token)
        s.commit()
    resp = Response(status_code=204)
    A.clear_session_cookie(request, resp)
    return resp


@router.get("/me", response_model=S.UserOut)
def me(user: User = Depends(A.current_user)):
    return user_out(user)


@router.post("/password", status_code=204, response_class=Response)
def change_password(body: S.PasswordChangeIn, request: Request, user: User = Depends(A.current_user),
                    s: Session = Depends(get_session)):
    user = s.merge(user)
    if not A.verify_password(user.password_hash, body.current_password):
        raise ApiError(400, "invalid_password", "目前密碼不正確")
    A.check_password_policy(body.new_password)
    user.password_hash = A.hash_password(body.new_password)
    A.revoke_user_sessions(s, user.id, keep_token=request.cookies.get(A.COOKIE_NAME))
    audit(s, user, "user.password_change", "user", user.id)
    s.commit()
    return Response(status_code=204)
