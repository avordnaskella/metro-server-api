from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    """Единый формат ошибок: {"error": {code, message, requestId, details}}."""

    def __init__(self, status: int, code: str, message: str, details: list[dict] | None = None,
                 headers: dict | None = None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers or {}


def error_response(request: Request, status: int, code: str, message: str,
                   details: list[dict] | None = None, headers: dict | None = None) -> JSONResponse:
    rid = getattr(request.state, "request_id", None)
    body: dict = {"code": code, "message": message, "requestId": rid}
    if details:
        body["details"] = details
    h = dict(headers or {})
    if rid:
        h["X-Request-Id"] = rid
    return JSONResponse({"error": body}, status_code=status, headers=h)


def unauthorized(code: str, message: str) -> AppError:
    return AppError(401, code, message, headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})


def forbidden(message: str = "Недостаточно прав для этого действия") -> AppError:
    return AppError(403, "FORBIDDEN", message)


def not_found(message: str = "Ресурс не найден") -> AppError:
    return AppError(404, "NOT_FOUND", message)


def validation(field: str, message: str, status: int = 400, code: str = "VALIDATION_ERROR",
               top_message: str = "Некорректные данные") -> AppError:
    return AppError(status, code, top_message, [{"field": field, "message": message}])


_MSG = {
    "missing": "Обязательное поле",
    "int_parsing": "Должно быть целым числом",
    "int_type": "Должно быть целым числом",
    "uuid_parsing": "Неверный формат UUID",
    "datetime_parsing": "Неверный формат даты и времени (ISO 8601)",
    "datetime_from_date_parsing": "Неверный формат даты и времени (ISO 8601)",
    "enum": "Недопустимое значение",
    "string_type": "Должно быть строкой",
    "list_type": "Должен быть список",
}


def _human(err: dict) -> str:
    t, ctx = err.get("type", ""), err.get("ctx", {}) or {}
    if t in _MSG:
        return _MSG[t]
    if t == "greater_than_equal":
        return f"Должно быть не меньше {ctx.get('ge')}"
    if t == "less_than_equal":
        return f"Должно быть не больше {ctx.get('le')}"
    if t == "string_too_long":
        return f"Слишком длинное значение (максимум {ctx.get('max_length')})"
    if t == "string_too_short":
        return f"Слишком короткое значение (минимум {ctx.get('min_length')})"
    if t == "json_invalid":
        return "Тело запроса не является корректным JSON"
    return err.get("msg", "Некорректное значение")


async def validation_handler(request: Request, exc: RequestValidationError):
    details = []
    for e in exc.errors():
        loc = [str(p) for p in e.get("loc", []) if p not in ("body", "query", "path")]
        details.append({"field": ".".join(loc) or "body", "message": _human(e)})
    return error_response(request, 400, "VALIDATION_ERROR", "Некорректные данные", details)


async def app_error_handler(request: Request, exc: AppError):
    return error_response(request, exc.status, exc.code, exc.message, exc.details, exc.headers)


_HTTP_CODES = {404: ("NOT_FOUND", "Ресурс не найден"), 405: ("METHOD_NOT_ALLOWED", "Метод не поддерживается")}


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    code, msg = _HTTP_CODES.get(exc.status_code, ("HTTP_ERROR", str(exc.detail)))
    return error_response(request, exc.status_code, code, msg)
