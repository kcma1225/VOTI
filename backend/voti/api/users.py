"""/api/users (admin only)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import auth as A
from ..db import get_session
from ..models import User
from . import schemas as S
from .errors import ApiError
from .views import audit, get_or_404, user_out

router = APIRouter(prefix="/users", tags=["users"])


def _snapshot(u: User) -> dict:
    return {"username": u.username, "display_name": u.display_name, "role": u.role, "is_active": u.is_active}


@router.get("", response_model=S.Page[S.UserOut])
def list_users(_: User = Depends(A.require_admin), s: Session = Depends(get_session)):
    users = s.scalars(select(User).order_by(User.created_at)).all()
    return S.Page[S.UserOut](items=[user_out(u) for u in users], total=len(users))


@router.post("", response_model=S.UserOut, status_code=201)
def create_user(body: S.UserCreateIn, admin: User = Depends(A.require_admin), s: Session = Depends(get_session)):
    if s.scalar(select(User.id).where(User.username == body.username)):
        raise ApiError(409, "username_taken", "帳號名稱已存在")
    A.check_password_policy(body.password)
    u = User(username=body.username, display_name=body.display_name or body.username,
             password_hash=A.hash_password(body.password), role=body.role, is_active=True)
    s.add(u)
    s.flush()
    audit(s, admin, "user.create", "user", u.id, after=_snapshot(u))
    s.commit()
    return user_out(u)


@router.patch("/{user_id}", response_model=S.UserOut)
def patch_user(user_id: uuid.UUID, body: S.UserPatchIn, admin: User = Depends(A.require_admin),
               s: Session = Depends(get_session)):
    u = get_or_404(s, User, user_id, "使用者")
    before = _snapshot(u)
    losing_admin = (body.role is not None and body.role != "admin") or body.is_active is False
    if losing_admin and u.role == "admin" and u.is_active:
        others = s.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True),
                                                                        User.id != u.id))
        if not others:
            raise ApiError(409, "last_admin", "不能停用或降級最後一位管理員")
    if body.display_name is not None:
        u.display_name = body.display_name
    if body.role is not None:
        u.role = body.role
    if body.is_active is not None:
        u.is_active = body.is_active
        if not body.is_active:
            A.revoke_user_sessions(s, u.id)
    changed_pw = False
    if body.password is not None:
        A.check_password_policy(body.password)
        u.password_hash = A.hash_password(body.password)
        A.revoke_user_sessions(s, u.id)
        changed_pw = True
    after = _snapshot(u)
    if changed_pw:
        after["password_changed"] = True
    audit(s, admin, "user.update", "user", u.id, before=before, after=after)
    s.commit()
    return user_out(u)
