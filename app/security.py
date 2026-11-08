import base64
import hashlib
import hmac
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import timedelta

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db, utcnow
from .errors import AppError, unauthorized
from .models import AuthSession, User

# ---------- пароли (scrypt из стандартной библиотеки) ----------
_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "scrypt${}${}${}${}${}".format(_N, _R, _P, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt_b64, dk_b64 = stored.split("$")
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(dk_b64)
        dk = hashlib.scrypt(password.encode(), salt=salt, n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


_DUMMY_HASH = hash_password("dummy-password-for-timing")

# ---------- JWT access + одноразовый refresh ----------


def create_access_token(user: User) -> str:
    now = utcnow()
    payload = {
        "sub": user.id,
        "role": user.role,
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.access_ttl_sec)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_alg)


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def issue_token_pair(db: Session, user: User) -> dict:
    refresh = str(uuid.uuid4())
    db.add(AuthSession(
        id=str(uuid.uuid4()), user_id=user.id, refresh_hash=_sha256(refresh),
        expires_at=utcnow() + timedelta(seconds=settings.refresh_ttl_sec),
    ))
    return {
        "accessToken": create_access_token(user),
        "refreshToken": refresh,
        "tokenType": "Bearer",
        "expiresIn": settings.access_ttl_sec,
        "user": user,
    }


def find_session(db: Session, refresh_token: str) -> AuthSession | None:
    return db.query(AuthSession).filter(AuthSession.refresh_hash == _sha256(refresh_token)).one_or_none()


# ---------- защита от перебора пароля ----------
_failures: dict[str, deque] = defaultdict(deque)


def check_login_allowed(key: str) -> None:
    q = _failures[key]
    now = time.monotonic()
    while q and now - q[0] > settings.login_window_sec:
        q.popleft()
    if len(q) >= settings.login_max_failures:
        retry = max(1, int(settings.login_window_sec - (now - q[0])))
        raise AppError(429, "TOO_MANY_REQUESTS", "Слишком много попыток входа. Подождите и повторите",
                       headers={"Retry-After": str(retry)})


def register_login_failure(key: str) -> None:
    _failures[key].append(time.monotonic())


def reset_login_failures(key: str) -> None:
    _failures.pop(key, None)


# ---------- текущий пользователь ----------
_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise unauthorized("INVALID_TOKEN", "Сессия истекла, войдите снова")
    try:
        payload = jwt.decode(creds.credentials, settings.jwt_secret, algorithms=[settings.jwt_alg],
                             options={"require": ["exp", "sub"]})
    except jwt.ExpiredSignatureError:
        raise unauthorized("TOKEN_EXPIRED", "Сессия истекла, войдите снова")
    except jwt.InvalidTokenError:
        raise unauthorized("INVALID_TOKEN", "Сессия истекла, войдите снова")
    # Роль берём из БД, а не из токена: смена роли/блокировка действуют сразу («роль проверяется на каждом запросе»)
    user = db.get(User, payload["sub"])
    if user is None or not user.is_active:
        raise unauthorized("INVALID_TOKEN", "Сессия истекла, войдите снова")
    return user


def require_roles(user: User, *roles: str) -> None:
    from .errors import forbidden
    if user.role not in roles:
        raise forbidden()
