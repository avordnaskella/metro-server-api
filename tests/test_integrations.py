import io, json, logging
from uuid import UUID

import httpx
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.integrations import install_reliability
from app.integrations.ai_router import build_ai_router
from app.integrations.ratelimit import limiter
from app.integrations.settings import ExtSettings, get_ext_settings, check_startup
from app.integrations.vision import AnthropicClassifier, DefectTypeRef

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 50
TYPES = [DefectTypeRef(1, "Трещина", 10), DefectTypeRef(2, "Болт", 11)]


class User:
    id = "u1"


def make_app(classifier=None, photo=JPEG, **env):
    get_ext_settings.cache_clear()
    limiter._hits.clear()
    app = FastAPI()
    install_reliability(app, "x" * 40)

    class Body(BaseModel):
        n: int

    @app.post("/echo")
    def echo(b: Body):
        return b

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret sk-ant-abc123456789 Bearer tok.en.value")

    app.include_router(build_ai_router(lambda: User(), lambda u, d: photo, lambda: TYPES, classifier))
    return TestClient(app, raise_server_exceptions=False)


def test_validation_error_format_and_no_input_echo():
    r = make_app().post("/echo", json={"n": "super-secret-password"})
    e = r.json()["error"]
    assert r.status_code == 400 and e["code"] == "VALIDATION_ERROR"
    assert e["details"][0]["field"] == "n" and "super-secret" not in r.text
    assert e["requestId"] == r.headers["x-request-id"]


def test_404_and_500_use_unified_format_and_mask_secrets(caplog):
    c = make_app()
    assert c.get("/nope").json()["error"]["code"] == "NOT_FOUND"
    with caplog.at_level(logging.ERROR):
        r = c.get("/boom")
    assert r.status_code == 500 and r.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "x-request-id" in r.headers and "boom" not in r.text.lower()
    logged = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "sk-ant-abc" not in logged


def test_request_id_is_propagated():
    r = make_app().get("/nope", headers={"X-Request-Id": "client-req-12345"})
    assert r.headers["x-request-id"] == "client-req-12345"


def test_global_rate_limit(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT", "3")
    c = make_app()
    codes = [c.get("/nope").status_code for _ in range(5)]
    assert codes == [404, 404, 404, 429, 429]
    r = c.get("/nope")
    assert r.json()["error"]["code"] == "TOO_MANY_REQUESTS" and int(r.headers["retry-after"]) >= 1


def test_ai_mock_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = make_app().post(f"/api/v1/defects/{UUID(int=1)}/ai-suggestion")
    assert r.status_code == 200 and r.json()["source"] == "mock"


def test_ai_requires_photo_and_rate_limit(monkeypatch):
    assert make_app(photo=None).post(f"/api/v1/defects/{UUID(int=1)}/ai-suggestion").status_code == 409
    monkeypatch.setenv("AI_RATE_PER_MIN", "2")
    c = make_app()
    codes = [c.post(f"/api/v1/defects/{UUID(int=1)}/ai-suggestion").status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def _clf(handler, retries=2):
    cfg = ExtSettings(anthropic_api_key="k", ai_max_retries=retries)
    return AnthropicClassifier(cfg, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def _ok(text):
    return httpx.Response(200, json={"content": [{"type": "text", "text": text}]})


def run(c, coro):
    import asyncio
    return asyncio.run(coro)


@pytest.mark.parametrize("out,err", [
    ('{"typeId": 99, "severity": "LOW", "summary": "x", "confidence": 0.9}', "unknown typeId"),
    ('{"typeId": 1, "severity": "HACK", "summary": "x", "confidence": 0.9}', "invalid model output"),
    ("не json", "invalid model output"),
])
def test_model_output_is_validated(out, err):
    from app.integrations.vision import AiBadResponse
    with pytest.raises(AiBadResponse, match=err):
        run(None, _clf(lambda r: _ok(out)).classify(JPEG, TYPES))


def test_success_retry_and_key_in_header_only():
    calls = []

    def h(req):
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(503)
        return _ok('```json\n{"typeId": 2, "severity": "CRITICAL", "summary": "ок", "confidence": 0.8}\n```')

    s = run(None, _clf(h).classify(JPEG, TYPES))
    assert len(calls) == 2 and s.categoryId == 11 and s.severity == "CRITICAL"
    assert calls[0].headers["x-api-key"] == "k" and b'"k"' not in calls[0].content


def test_circuit_breaker_opens_then_503():
    from app.integrations.vision import AiUnavailable
    n = []
    clf = _clf(lambda r: (n.append(1), httpx.Response(500))[1], retries=0)
    for _ in range(5):
        with pytest.raises(AiUnavailable):
            run(None, clf.classify(JPEG, TYPES))
    before = len(n)
    with pytest.raises(AiUnavailable):
        run(None, clf.classify(JPEG, TYPES))
    assert len(n) == before          # провайдера уже не дёргаем


def test_prod_refuses_default_secret(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    get_ext_settings.cache_clear()
    with pytest.raises(RuntimeError):
        check_startup("change-me-to-a-long-random-string-at-least-32-chars")
    get_ext_settings.cache_clear()
