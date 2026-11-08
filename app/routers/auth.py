from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from ..db import get_db, utcnow
from ..errors import AppError, unauthorized
from ..models import User
from ..schemas import LoginRequest, RefreshRequest, TokenPair
from ..security import (_DUMMY_HASH, check_login_allowed, find_session, get_current_user, issue_token_pair,
                        register_login_failure, reset_login_failures, verify_password)

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/login", response_model=TokenPair)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    key = f"{body.login.lower()}|{request.client.host if request.client else '-'}"
    check_login_allowed(key)
    user = db.query(User).filter(User.login == body.login).one_or_none()
    # хеш считается всегда, чтобы по времени ответа нельзя было отличить «нет такого логина»
    ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok or not user.is_active:
        register_login_failure(key)
        raise AppError(401, "INVALID_CREDENTIALS", "Неверный логин или пароль")
    reset_login_failures(key)
    pair = issue_token_pair(db, user)
    db.commit()
    return pair


@router.post("/refresh", response_model=TokenPair)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    """Refresh одноразовый: старая сессия удаляется, выдаётся новая пара (ротация)."""
    session = find_session(db, body.refresh_token)
    if session is None or session.expires_at <= utcnow():
        if session is not None:
            db.delete(session)
            db.commit()
        raise unauthorized("INVALID_TOKEN", "Сессия истекла, войдите снова")
    user = db.get(User, session.user_id)
    db.delete(session)
    if user is None or not user.is_active:
        db.commit()
        raise unauthorized("INVALID_TOKEN", "Сессия истекла, войдите снова")
    pair = issue_token_pair(db, user)
    db.commit()
    return pair


@router.post("/logout", status_code=204)
def logout(body: RefreshRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    session = find_session(db, body.refresh_token)
    # чужую сессию завершить нельзя — для не-владельца она «не существует»
    if session is None or session.user_id != user.id:
        raise unauthorized("INVALID_TOKEN", "Сессия истекла, войдите снова")
    db.delete(session)
    db.commit()
    return Response(status_code=204)
