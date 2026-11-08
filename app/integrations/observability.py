"""Структурное логирование + сквозной requestId (чистый ASGI, без BaseHTTPMiddleware)."""
from __future__ import annotations

import contextvars
import json
import logging
import re
import time
import uuid

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
user_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("user_id", default="-")

_RID_OK = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
# Маскируем всё, что похоже на секреты, даже если кто-то залогирует их по ошибке.
_SECRET_RE = re.compile(r"(Bearer\s+)[\w.\-]+|(sk-ant-[\w\-]+)|(api[_-]?key[\"'=:\s]+)[\w\-]{8,}", re.I)


def _mask(text: str) -> str:
    return _SECRET_RE.sub(lambda m: (m.group(1) or m.group(3) or "") + "***", text)


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.user_id = user_id_var.get()
        record.msg = _mask(record.getMessage())
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, r: logging.LogRecord) -> str:
        d = {
            "ts": self.formatTime(r, "%Y-%m-%dT%H:%M:%S%z"),
            "level": r.levelname,
            "logger": r.name,
            "msg": r.getMessage(),
            "requestId": getattr(r, "request_id", "-"),
            "userId": getattr(r, "user_id", "-"),
        }
        d.update(getattr(r, "fields", {}))
        if r.exc_info:
            d["exc"] = _mask(self.formatException(r.exc_info))
        return json.dumps(d, ensure_ascii=False)


def configure_logging(level: str = "INFO", as_json: bool = False) -> None:
    h = logging.StreamHandler()
    h.addFilter(ContextFilter())
    h.setFormatter(JsonFormatter() if as_json else logging.Formatter(
        "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(level)
    logging.getLogger("uvicorn.access").disabled = True   # свой access-лог ниже
    logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx пишет URL запросов


access_log = logging.getLogger("app.access")


class RequestContextMiddleware:
    """requestId, access-лог (без query и тела: там могут быть персональные данные),
    и гарантированный JSON-ответ 500 в едином формате."""

    def __init__(self, app, skip_paths=("/health",)):
        self.app, self.skip = app, set(skip_paths)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        hdrs = {k.decode().lower(): v.decode("latin-1") for k, v in scope["headers"]}
        rid = hdrs.get("x-request-id", "")
        rid = rid if _RID_OK.match(rid) else uuid.uuid4().hex[:16]
        request_id_var.set(rid)
        user_id_var.set("-")
        started, status = time.perf_counter(), 500

        async def send_wrapper(msg):
            nonlocal status
            if msg["type"] == "http.response.start":
                status = msg["status"]
                msg.setdefault("headers", []).append((b"x-request-id", rid.encode()))
            await send(msg)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:  # непойманное исключение → единый формат 500
            logging.getLogger("app.errors").exception("unhandled error")
            from .errors import error_response
            await error_response(500, "INTERNAL_ERROR",
                                 "На сервере произошла ошибка. Повторите попытку позже")(scope, receive, send_wrapper)
        finally:
            if scope["path"] not in self.skip:
                ms = round((time.perf_counter() - started) * 1000, 1)
                client = (scope.get("client") or ("-", 0))[0]
                lvl = logging.ERROR if status >= 500 else logging.WARNING if status >= 400 else logging.INFO
                access_log.log(lvl, "%s %s -> %s (%s ms)", scope["method"], scope["path"], status, ms,
                               extra={"fields": {"method": scope["method"], "path": scope["path"],
                                                 "status": status, "durationMs": ms, "ip": client}})
