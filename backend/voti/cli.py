"""Admin CLI.

  python -m voti.cli init                         create tables/extensions, bucket, initial admin (from env)
  python -m voti.cli create-user NAME [--role admin|annotator] [--display-name X]   (password prompted or
                                                  read from VOTI_PASSWORD)
  python -m voti.cli reset-password NAME          (password prompted or VOTI_PASSWORD)
"""
from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys

from sqlalchemy import select

from . import storage
from .auth import MIN_PASSWORD_LEN, ROLES, bootstrap_admin, hash_password, revoke_user_sessions
from .db import init_db, session_scope
from .models import User


def _password(prompt_twice: bool = True) -> str:
    pw = os.environ.get("VOTI_PASSWORD")
    if pw:
        return pw
    pw = getpass.getpass("密碼：")
    if prompt_twice and getpass.getpass("再輸入一次：") != pw:
        sys.exit("兩次輸入的密碼不一致")
    return pw


def _check(pw: str) -> None:
    if len(pw) < MIN_PASSWORD_LEN:
        sys.exit(f"密碼至少需要 {MIN_PASSWORD_LEN} 個字元")


def cmd_init(_args: argparse.Namespace) -> int:
    init_db()
    print("database: ok")
    storage.ensure_bucket()
    print(f"bucket {storage.bucket()}: ok")
    with session_scope() as s:
        u = bootstrap_admin(s)
        if u:
            print(f"created initial admin {u.username!r}")
        elif s.scalar(select(User.id).limit(1)) is None:
            print("WARNING: no users exist and ADMIN_PASSWORD is not set; run `python -m voti.cli create-user`",
                  file=sys.stderr)
            return 1
        else:
            print("users exist: initial admin not (re)applied")
    return 0


def cmd_create_user(args: argparse.Namespace) -> int:
    init_db()
    pw = _password()
    _check(pw)
    with session_scope() as s:
        if s.scalar(select(User.id).where(User.username == args.username)):
            sys.exit(f"使用者 {args.username} 已存在")
        s.add(User(username=args.username, display_name=args.display_name or args.username,
                   password_hash=hash_password(pw), role=args.role, is_active=True))
    print(f"created {args.role} {args.username!r}")
    return 0


def cmd_reset_password(args: argparse.Namespace) -> int:
    pw = _password()
    _check(pw)
    with session_scope() as s:
        u = s.scalar(select(User).where(User.username == args.username))
        if u is None:
            sys.exit(f"找不到使用者 {args.username}")
        u.password_hash = hash_password(pw)
        u.is_active = True
        revoke_user_sessions(s, u.id)
    print(f"password reset for {args.username!r} (all sessions revoked)")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="voti")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create tables, bucket and the initial admin").set_defaults(fn=cmd_init)
    c = sub.add_parser("create-user", help="create a user")
    c.add_argument("username")
    c.add_argument("--role", choices=ROLES, default="annotator")
    c.add_argument("--display-name", default="")
    c.set_defaults(fn=cmd_create_user)
    r = sub.add_parser("reset-password", help="set a new password (and re-activate the user)")
    r.add_argument("username")
    r.set_defaults(fn=cmd_reset_password)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
