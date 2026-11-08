"""Настройки интеграций и надёжности. Всё — только из переменных окружения."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

_DEFAULT_SECRETS = {"", "change-me-to-a-long-random-string-at-least-32-chars"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        raise RuntimeError(f"{name} должно быть целым числом")


@dataclass(frozen=True)
class ExtSettings:
    env: str = "development"                      # development | production
    log_level: str = "INFO"
    log_json: bool = False
    # ИИ-API (ключ НИКОГДА не попадает в repr/логи)
    ai_enabled: bool = True
    anthropic_api_key: str = field(default="", repr=False)
    anthropic_model: str = "claude-haiku-5-5"
    anthropic_url: str = "https://api.anthropic.com/v1/messages"
    ai_timeout_s: float = 20.0
    ai_max_retries: int = 2
    ai_rate_per_min: int = 5                      # на пользователя
    ai_daily_quota: int = 100                     # на пользователя
    # общий лимит на IP
    rate_limit: int = 120
    rate_window_s: int = 60

    @property
    def is_prod(self) -> bool:
        return self.env == "production"

    @property
    def ai_mock(self) -> bool:
        """Нет ключа → заглушка: локально и в тестах платное API не вызывается."""
        return not self.anthropic_api_key


@lru_cache
def get_ext_settings() -> ExtSettings:
    e = os.environ
    return ExtSettings(
        env=e.get("APP_ENV", "development").lower(),
        log_level=e.get("LOG_LEVEL", "INFO").upper(),
        log_json=e.get("LOG_JSON", "false").lower() == "true",
        ai_enabled=e.get("AI_ENABLED", "true").lower() == "true",
        anthropic_api_key=e.get("ANTHROPIC_API_KEY", ""),
        anthropic_model=e.get("ANTHROPIC_MODEL", "claude-haiku-5-5"),
        ai_timeout_s=float(e.get("AI_TIMEOUT_S", 20)),
        ai_max_retries=_int("AI_MAX_RETRIES", 2),
        ai_rate_per_min=_int("AI_RATE_PER_MIN", 5),
        ai_daily_quota=_int("AI_DAILY_QUOTA", 100),
        rate_limit=_int("RATE_LIMIT", 120),
        rate_window_s=_int("RATE_WINDOW_S", 60),
    )


def check_startup(jwt_secret: str) -> None:
    """Fail fast: в проде не стартуем с дефолтным/коротким секретом."""
    s = get_ext_settings()
    if s.is_prod and (jwt_secret in _DEFAULT_SECRETS or len(jwt_secret) < 32):
        raise RuntimeError("APP_ENV=production: задайте JWT_SECRET (>= 32 символов)")
