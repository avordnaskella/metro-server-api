from fastapi import FastAPI

from .errors import ApiError, install_error_handlers
from .observability import RequestContextMiddleware, configure_logging
from .ratelimit import GlobalRateLimitMiddleware
from .settings import check_startup, get_ext_settings

__all__ = ["ApiError", "install_reliability"]


def install_reliability(app: FastAPI, jwt_secret: str) -> None:
    """Один вызов в app/main.py сразу после создания FastAPI()."""
    cfg = get_ext_settings()
    check_startup(jwt_secret)
    configure_logging(cfg.log_level, cfg.log_json)
    install_error_handlers(app)
    # Порядок: добавленный последним — самый внешний. requestId/лог должны видеть и 429.
    app.add_middleware(GlobalRateLimitMiddleware, limit=cfg.rate_limit, window_s=cfg.rate_window_s)
    app.add_middleware(RequestContextMiddleware)
