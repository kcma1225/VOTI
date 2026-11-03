"""Uniform API errors: every error body is {"detail": "<zh-TW message>", "code": "<machine code>"}."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException


class ErrorBody(BaseModel):
    detail: str
    code: str


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: str, headers: dict[str, str] | None = None):
        super().__init__(detail)
        self.status, self.code, self.detail, self.headers = status, code, detail, headers


def not_found(what: str = "資料") -> ApiError:
    return ApiError(404, "not_found", f"找不到{what}")


def bad_request(code: str, detail: str) -> ApiError:
    return ApiError(400, code, detail)


def conflict(code: str, detail: str) -> ApiError:
    return ApiError(409, code, detail)


DEFAULT_MESSAGES = {
    400: "請求錯誤", 401: "尚未登入", 403: "沒有權限", 404: "找不到資料", 405: "不支援的請求方法",
    409: "資料狀態衝突", 413: "檔案太大", 416: "請求範圍無效", 422: "請求參數格式錯誤", 429: "請求太頻繁，請稍後再試",
    500: "伺服器內部錯誤", 503: "服務暫時無法使用",
}


def error_response(status: int, code: str, detail: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"detail": detail, "code": code}, status_code=status, headers=headers)


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError):  # noqa: ANN202
        return error_response(exc.status, exc.code, exc.detail, exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_req: Request, exc: StarletteHTTPException):  # noqa: ANN202
        detail = exc.detail if isinstance(exc.detail, str) and exc.detail and not exc.detail.isascii() else \
            DEFAULT_MESSAGES.get(exc.status_code, "請求失敗")
        return error_response(exc.status_code, f"http_{exc.status_code}", detail, getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, exc: RequestValidationError):  # noqa: ANN202
        return JSONResponse({"detail": DEFAULT_MESSAGES[422], "code": "validation_error",
                             "errors": jsonable_encoder(exc.errors())}, status_code=422)
