import base64
import json
import os
import uuid
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import String, and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db, utcnow
from ..errors import AppError, forbidden, not_found, validation
from ..models import Defect, DefectCategory, DefectType, Section, StatusHistory, Track, User
from ..policies import DISPATCHER, FOREMAN, WORKER, can_edit_defect, is_my_shift, visible_defects
from ..schemas import (DefectCreate, DefectDetails, DefectOut, DefectPage, DefectStatus, DefectUpdate, Severity,
                       StatusChangeOut, StatusChangeRequest)
from ..security import get_current_user, require_roles

router = APIRouter(prefix="/defects", tags=["Defects"])

NOT_FOUND_MSG = "Неисправность не найдена"
TRANSITIONS = {
    "NEW": {"IN_PROGRESS"},
    "IN_PROGRESS": {"FIXED"},
    "FIXED": {"VERIFIED", "IN_PROGRESS"},  # возврат при отклонении проверки
    "VERIFIED": set(),
}
SORTS = {"detectedAt", "-detectedAt", "picket", "-picket"}


# ---------- вспомогательное ----------
def _photo_url(d: Defect) -> str | None:
    if not d.photo_path:
        return None
    # Фото отдаётся только через авторизованный эндпоинт (RLS действует и на файлы);
    # ?v= сбрасывает кеш клиента (Coil) при замене фото.
    v = int(d.photo_updated_at.timestamp()) if d.photo_updated_at else 0
    return f"{settings.public_base_url}/api/v1/defects/{d.id}/photo?v={v}"


def _base(d: Defect) -> dict:
    return dict(
        id=d.id, user_id=d.user_id, shift_id=d.shift_id, section_id=d.section_id, track_id=d.track_id,
        category_id=d.category_id, type_id=d.type_id, picket=d.picket, severity=d.severity, status=d.status,
        comment=d.comment or "", detected_at=d.detected_at, photo_url=_photo_url(d),
    )


def out(d: Defect) -> DefectOut:
    return DefectOut(**_base(d))


def details(d: Defect) -> DefectDetails:
    hist = [StatusChangeOut.model_validate(h) for h in d.history]
    return DefectDetails(**_base(d), history=hist)


def get_visible(db: Session, user: User, defect_id: UUID, lock: bool = False) -> Defect:
    stmt = select(Defect).where(Defect.id == str(defect_id), visible_defects(user))
    if lock:
        stmt = stmt.with_for_update()
    d = db.scalars(stmt).one_or_none()
    if d is None:
        raise not_found(NOT_FOUND_MSG)
    return d


def check_picket(db: Session, section_id: str, picket: int) -> None:
    sec = db.get(Section, section_id)
    if not sec.start_m <= picket <= sec.end_m:
        raise AppError(422, "PICKET_OUT_OF_RANGE", "Пикет вне границ участка",
                       [{"field": "picket", "message": f"Допустимый диапазон {sec.start_m}–{sec.end_m} м"}])


def _enc_cursor(sort: str, key, i: str) -> str:
    k = key.astimezone(timezone.utc).isoformat() if isinstance(key, datetime) else key
    raw = json.dumps({"s": sort, "k": k, "i": i}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _dec_cursor(cursor: str, sort: str):
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if data["s"] != sort:
            raise ValueError("sort mismatch")
        key = datetime.fromisoformat(data["k"]) if sort.lstrip("-") == "detectedAt" else int(data["k"])
        return key, str(data["i"])
    except Exception:
        raise validation("cursor", "Недействительный курсор (или параметр sort изменился)")


# ---------- список ----------
@router.get("", response_model=DefectPage)
def list_defects(
    section_id: UUID | None = Query(None, alias="sectionId"),
    track_id: UUID | None = Query(None, alias="trackId"),
    status: str | None = Query(None, description="Через запятую: NEW,IN_PROGRESS"),
    category_id: int | None = Query(None, alias="categoryId"),
    type_id: int | None = Query(None, alias="typeId"),
    severity: Severity | None = None,
    picket_from: int | None = Query(None, alias="picketFrom", ge=0),
    picket_to: int | None = Query(None, alias="picketTo", ge=0),
    q: str | None = Query(None, max_length=100),
    detected_from: datetime | None = Query(None, alias="detectedFrom"),
    detected_to: datetime | None = Query(None, alias="detectedTo"),
    shift_id: UUID | None = Query(None, alias="shiftId"),
    sort: str = "-detectedAt",
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if sort not in SORTS:
        raise validation("sort", "Допустимо: detectedAt, -detectedAt, picket, -picket")

    conds = [visible_defects(user)]  # RLS
    if section_id: conds.append(Defect.section_id == str(section_id))
    if track_id: conds.append(Defect.track_id == str(track_id))
    if category_id is not None: conds.append(Defect.category_id == category_id)
    if type_id is not None: conds.append(Defect.type_id == type_id)
    if severity: conds.append(Defect.severity == severity.value)
    if picket_from is not None: conds.append(Defect.picket >= picket_from)
    if picket_to is not None: conds.append(Defect.picket <= picket_to)
    if detected_from: conds.append(Defect.detected_at >= detected_from)
    if detected_to: conds.append(Defect.detected_at <= detected_to)
    if shift_id: conds.append(Defect.shift_id == str(shift_id))
    if q:
        conds.append(func.lower(Defect.comment, type_=String).contains(q.lower(), autoescape=True))
    if status:
        values = [s.strip() for s in status.split(",") if s.strip()]
        bad = [s for s in values if s not in DefectStatus.__members__]
        if bad or not values:
            raise validation("status", "Допустимо: NEW, IN_PROGRESS, FIXED, VERIFIED (через запятую)")
        conds.append(Defect.status.in_(values))

    field, desc = sort.lstrip("-"), sort.startswith("-")
    col = Defect.detected_at if field == "detectedAt" else Defect.picket
    if cursor:  # keyset: (col, id) строго «после» последней записи предыдущей страницы
        key, last_id = _dec_cursor(cursor, sort)
        if desc:
            conds.append(or_(col < key, and_(col == key, Defect.id < last_id)))
        else:
            conds.append(or_(col > key, and_(col == key, Defect.id > last_id)))

    order = (col.desc(), Defect.id.desc()) if desc else (col.asc(), Defect.id.asc())
    rows = db.scalars(select(Defect).where(*conds).order_by(*order).limit(limit + 1)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = _enc_cursor(sort, last.detected_at if field == "detectedAt" else last.picket, last.id)
    return DefectPage(items=[out(d) for d in rows], next_cursor=next_cursor, has_more=has_more)


# ---------- создание (идемпотентно по id) ----------
@router.post("", response_model=DefectOut, status_code=201)
def create_defect(body: DefectCreate, response: Response, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    require_roles(user, WORKER, FOREMAN)
    did = str(body.id)

    existing = db.get(Defect, did)
    if existing is not None:
        if existing.user_id == user.id:  # повторная отправка той же записи
            response.status_code = 200
            return out(existing)
        raise AppError(409, "DEFECT_ID_CONFLICT", "Неисправность с таким кодом уже существует")

    section = db.get(Section, str(body.section_id))
    if section is None:
        raise validation("sectionId", "Участок не найден")
    track = db.get(Track, str(body.track_id))
    if track is None or track.section_id != section.id:
        raise validation("trackId", "Путь не найден на выбранном участке")
    if db.get(DefectCategory, body.category_id) is None:
        raise validation("categoryId", "Категория не найдена")
    dtype = db.get(DefectType, body.type_id)
    if dtype is None or dtype.category_id != body.category_id:
        raise validation("typeId", "Тип неисправности не относится к выбранной категории")
    if body.shift_id is not None:
        sid = str(body.shift_id)
        # чужие/несуществующие смены не различаем — для пользователя их «нет»
        if not is_my_shift(db, user, sid):
            raise validation("shiftId", "Смена не найдена")
        from ..models import Shift
        if db.get(Shift, sid).section_id != section.id:
            raise validation("shiftId", "Смена относится к другому участку")
    check_picket(db, section.id, body.picket)

    d = Defect(
        id=did, user_id=user.id, shift_id=str(body.shift_id) if body.shift_id else None,
        section_id=section.id, track_id=track.id, category_id=body.category_id, type_id=body.type_id,
        picket=body.picket, severity=body.severity.value, status="NEW", comment=body.comment,
        detected_at=body.detected_at or utcnow(),
    )
    db.add(d)
    try:
        db.commit()
    except IntegrityError:  # гонка двух одинаковых запросов
        db.rollback()
        existing = db.get(Defect, did)
        if existing is not None and existing.user_id == user.id:
            response.status_code = 200
            return out(existing)
        raise AppError(409, "DEFECT_ID_CONFLICT", "Неисправность с таким кодом уже существует")
    response.headers["Location"] = f"/api/v1/defects/{did}"
    return out(d)


# ---------- карточка ----------
@router.get("/{defect_id}", response_model=DefectDetails)
def get_defect(defect_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return details(get_visible(db, user, defect_id))


# ---------- изменение полей ----------
@router.patch("/{defect_id}", response_model=DefectOut)
def update_defect(defect_id: UUID, body: DefectUpdate, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    d = get_visible(db, user, defect_id, lock=True)
    if not can_edit_defect(db, user, d):
        raise forbidden()
    changes = body.model_dump(exclude_unset=True)

    if "track_id" in changes:
        t = db.get(Track, str(changes["track_id"]))
        if t is None or t.section_id != d.section_id:
            raise validation("trackId", "Путь не найден на участке неисправности")
        d.track_id = t.id
    if "type_id" in changes:
        t = db.get(DefectType, changes["type_id"])
        if t is None or t.category_id != d.category_id:
            raise validation("typeId", "Тип неисправности не относится к категории записи")
        d.type_id = t.id
    if "picket" in changes:
        check_picket(db, d.section_id, changes["picket"])
        d.picket = changes["picket"]
    if "severity" in changes:
        d.severity = changes["severity"].value
    if "comment" in changes:
        d.comment = changes["comment"]
    db.commit()
    return out(d)


# ---------- смена статуса ----------
@router.post("/{defect_id}/status", response_model=DefectDetails)
def change_status(defect_id: UUID, body: StatusChangeRequest, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    d = get_visible(db, user, defect_id, lock=True)
    to = body.to_status.value

    if body.id is not None:  # идемпотентность: тот же код записи истории уже применён
        prev = db.get(StatusHistory, str(body.id))
        if prev is not None:
            if prev.defect_id == d.id:
                return details(d)
            raise AppError(409, "DEFECT_ID_CONFLICT", "Код записи истории уже занят")

    if to not in TRANSITIONS[d.status]:
        raise AppError(409, "INVALID_STATUS_TRANSITION", f"Нельзя перейти из {d.status} сразу в {to}")
    if to == "VERIFIED":
        require_roles(user, FOREMAN, DISPATCHER)

    d.history.append(StatusHistory(
        id=str(body.id or uuid.uuid4()), defect_id=d.id, user_id=user.id, status=to,
        comment=body.comment, changed_at=utcnow(),
    ))
    d.status = to
    db.commit()
    return details(d)


# ---------- фото ----------
_MAGIC = ((b"\xff\xd8\xff", ".jpg", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", ".png", "image/png"))


@router.post("/{defect_id}/photo", response_model=DefectOut)
def upload_photo(defect_id: UUID, file: UploadFile = File(...), user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    require_roles(user, WORKER, FOREMAN)
    d = get_visible(db, user, defect_id)

    data = file.file.read(settings.max_photo_bytes + 1)
    if len(data) > settings.max_photo_bytes:
        raise AppError(413, "PAYLOAD_TOO_LARGE", "Размер файла превышает 5 МБ")
    if not data:
        raise validation("file", "Пустой файл")
    kind = next(((ext, mime) for sig, ext, mime in _MAGIC if data.startswith(sig)), None)  # по содержимому, не по заголовку
    if kind is None:
        raise AppError(415, "UNSUPPORTED_MEDIA_TYPE", "Допустимы только JPEG и PNG")

    photos = settings.storage_dir / "photos"
    photos.mkdir(parents=True, exist_ok=True)
    name = f"{d.id}{kind[0]}"
    tmp = photos / f".{name}.{uuid.uuid4().hex}.tmp"
    tmp.write_bytes(data)
    os.replace(tmp, photos / name)  # атомарная замена: повторная загрузка безопасна
    if d.photo_path and d.photo_path != name:
        (photos / d.photo_path).unlink(missing_ok=True)
    d.photo_path, d.photo_updated_at = name, utcnow()
    db.commit()
    return out(d)


@router.get("/{defect_id}/photo")
def get_photo(defect_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    d = get_visible(db, user, defect_id)
    if not d.photo_path:
        raise not_found("Фото не найдено")
    path = settings.storage_dir / "photos" / d.photo_path
    if not path.exists():
        raise not_found("Фото не найдено")
    mime = "image/png" if d.photo_path.endswith(".png") else "image/jpeg"
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "private, max-age=86400"})
