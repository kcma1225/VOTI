"""FastAPI application: routers under /api, host allowlist, same-origin check for writes, security headers.

Run: uvicorn voti.api.app:app --proxy-headers --forwarded-allow-ips '*'
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI

from .. import storage
from ..auth import bootstrap_admin, current_user
from ..config import get_config
from ..db import init_db, session_scope
from . import errors
from . import auth as auth_routes
from . import (audio, channels, datasets, jobs, media, quality, segments, settings, sources, speakers, users)

log = logging.getLogger("voti.api")

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def host_allowed(host_header: str, allowed: list[str]) -> bool:
    host = host_header.strip().lower()
    if host.startswith("["):  # [ipv6]:port
        host = host.split("]")[0] + "]"
    else:
        host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    for pattern in allowed:
        p = pattern.strip().lower()
        if p == "*" or host == p or (p.startswith("*.") and host.endswith(p[1:])):
            return True
    return False


def same_origin(headers: dict[str, str]) -> bool:
    """CSRF check for state-changing requests.

    Sec-Fetch-Site is authoritative when the browser sends it. Otherwise the Origin's host:port must equal Host.
    Requests with neither header are refused (all current browsers send Origin on non-GET requests; scripts
    must send an Origin header matching the host)."""
    site = headers.get("sec-fetch-site")
    if site is not None:
        return site in ("same-origin", "none")
    origin = headers.get("origin")
    if not origin or origin == "null":
        return False
    return urlsplit(origin).netloc.lower() == headers.get("host", "").lower()


class GuardMiddleware:
    """Host allowlist, cross-site write rejection, security headers. Pure ASGI (no body buffering)."""

    HEADERS = [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"same-origin"),
               (b"x-frame-options", b"DENY"),
               (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'")]

    def __init__(self, app, allowed_hosts: list[str]):  # noqa: ANN001
        self.app, self.allowed_hosts = app, allowed_hosts

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        if not host_allowed(headers.get("host", ""), self.allowed_hosts):
            return await self._reject(send, 400, "invalid_host", "不允許的主機名稱")
        if scope["method"] not in SAFE_METHODS and not same_origin(headers):
            return await self._reject(send, 403, "cross_site", "拒絕跨站請求：請從本站頁面操作")

        extra = self.HEADERS if scope.get("path") != "/api/docs" else self.HEADERS[:3]  # swagger UI needs CDN js

        async def wrapped(message):  # noqa: ANN001, ANN202
            if message["type"] == "http.response.start":
                hs = list(message.get("headers", []))
                present = {k.lower() for k, _ in hs}
                hs += [(k, v) for k, v in extra if k not in present]
                message["headers"] = hs
            await send(message)
        await self.app(scope, receive, wrapped)

    async def _reject(self, send, status: int, code: str, detail: str) -> None:  # noqa: ANN001
        body = json.dumps({"detail": detail, "code": code}, ensure_ascii=False).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json; charset=utf-8"),
                                (b"content-length", str(len(body)).encode())] + self.HEADERS})
        await send({"type": "http.response.body", "body": body})


def startup() -> None:
    init_db()
    try:
        storage.ensure_bucket()
    except Exception:  # noqa: BLE001
        log.exception("object storage not reachable at startup")
    with session_scope() as s:
        bootstrap_admin(s)


@asynccontextmanager
async def lifespan(_app: FastAPI):  # noqa: ANN201
    startup()
    yield


def create_app(run_startup: bool = True) -> FastAPI:
    cfg = get_config()
    app = FastAPI(title="VOTI API", version="2.0", openapi_url="/api/openapi.json", docs_url="/api/docs",
                  redoc_url=None, lifespan=lifespan if run_startup else None,
                  responses={401: {"model": errors.ErrorBody}, 403: {"model": errors.ErrorBody}})
    errors.install(app)

    api = APIRouter(prefix="/api")
    api.include_router(jobs.router)  # includes /healthz (public)
    api.include_router(auth_routes.router)  # login is public; the rest use current_user per route
    protected = [users, settings, sources, channels, media, segments, audio, speakers, quality, datasets]
    for mod in protected:
        api.include_router(mod.router, dependencies=[Depends(current_user)])
    app.include_router(api)
    app.add_middleware(GuardMiddleware, allowed_hosts=cfg.allowed_hosts)
    return app


app = create_app()
