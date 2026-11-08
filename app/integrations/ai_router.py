"""POST /api/v1/defects/{defectId}/ai-suggestion — подсказка типа/серьёзности по фото.
Подсказка — только совет: клиент показывает её, пользователь подтверждает сам."""
from __future__ import annotations

import hashlib
import logging
from collections import OrderedDict
from typing import Awaitable, Callable
from uuid import UUID

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from .errors import ApiError
from .observability import user_id_var
from .ratelimit import limiter
from .settings import get_ext_settings
from .vision import AiBadResponse, AiUnavailable, DefectTypeRef, Suggestion, make_classifier

log = logging.getLogger("app.ai")


def build_ai_router(
    get_current_user: Callable,                       # существующая Depends-функция авторизации
    load_photo: Callable[[object, UUID], bytes | None],   # (user, defect_id) -> байты фото; 404 по RLS кидает она
    list_types: Callable[[], list[DefectTypeRef]],
    classifier=None,
) -> APIRouter:
    cfg = get_ext_settings()
    clf = classifier or make_classifier(cfg)
    cache: OrderedDict[str, Suggestion] = OrderedDict()      # sha256(фото) → ответ: не платим дважды
    router = APIRouter(prefix="/api/v1", tags=["AI"])

    @router.post("/defects/{defect_id}/ai-suggestion", response_model=Suggestion,
                 operation_id="aiSuggestion")
    async def ai_suggestion(defect_id: UUID, user=Depends(get_current_user)):
        if not cfg.ai_enabled:
            raise ApiError(503, "AI_DISABLED", "Подсказки ИИ отключены")
        uid = str(getattr(user, "id", user))
        user_id_var.set(uid)
        limiter.check(f"ai:min:{uid}", cfg.ai_rate_per_min, 60)
        limiter.check(f"ai:day:{uid}", cfg.ai_daily_quota, 86400)

        photo = await run_in_threadpool(load_photo, user, defect_id)   # RLS внутри
        if not photo:
            raise ApiError(409, "PHOTO_REQUIRED", "Сначала загрузите фото неисправности")

        key = hashlib.sha256(photo).hexdigest()
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        try:
            s = await clf.classify(photo, await run_in_threadpool(list_types))
        except AiUnavailable as e:
            raise ApiError(503, "AI_UNAVAILABLE", "Сервис подсказок временно недоступен. Повторите позже",
                           headers={"Retry-After": str(e.retry_after)})
        except AiBadResponse:
            raise ApiError(502, "AI_BAD_RESPONSE", "Не удалось получить подсказку. Заполните поля вручную")
        cache[key] = s
        if len(cache) > 200:
            cache.popitem(last=False)
        log.info("ai suggestion ok source=%s conf=%.2f", s.source, s.confidence)
        return s

    return router
