"""Единый формат ошибок: {"error": {code, message, requestId, details}}."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .observability import request_id_var


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: list | None = None,
                 headers: dict | None = None):
        self.status, self.code, self.message = status, code, message
        self.details, self.headers = details or [], headers or {}


def error_response(status: int, code: str, message: str, details=None, headers=None) -> JSONResponse:
    body = {"error": {"code": code, "message": message,
                      "requestId": request_id_var.get(), "details": details or []}}
    return JSONResponse(body, status_code=status, headers=headers)


_HTTP_CODES = {400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND",
               405: "METHOD_NOT_ALLOWED", 409: "CONFLICT", 413: "PAYLOAD_TOO_LARGE",
               415: "UNSUPPORTED_MEDIA_TYPE", 429: "TOO_MANY_REQUESTS"}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, e: ApiError):
        return error_response(e.status, e.code, e.message, e.details, e.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError):
        # Значения полей (input) в ответ не эхом: там могут быть пароли/токены.
        details = [{"field": ".".join(str(p) for p in err["loc"][1:]) or str(err["loc"][0]),
                    "message": err["msg"]} for err in e.errors()]
        return error_response(400, "VALIDATION_ERROR", "Неверный формат или отсутствует поле", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException):
        code = _HTTP_CODES.get(e.status_code, f"HTTP_{e.status_code}")
        return error_response(e.status_code, code, str(e.detail), headers=getattr(e, "headers", None))
