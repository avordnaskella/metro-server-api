from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db, utcnow
from ..errors import AppError, not_found, validation
from ..models import Defect, Section, Shift, ShiftMember, User
from ..policies import FOREMAN, visible_defects, visible_shifts
from ..schemas import ShiftCreate, ShiftOut, ShiftSummary
from ..security import get_current_user, require_roles

router = APIRouter(prefix="/shifts", tags=["Shifts"])


def _out(s: Shift) -> dict:
    return dict(id=s.id, section_id=s.section_id, foreman_id=s.foreman_id,
                member_ids=[m.user_id for m in s.members], inspected_at=s.inspected_at)


@router.post("", response_model=ShiftOut, status_code=201)
def open_shift(body: ShiftCreate, response: Response, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    require_roles(user, FOREMAN)
    sid = str(body.id)

    existing = db.get(Shift, sid)
    if existing is not None:
        if existing.foreman_id == user.id:  # повторная отправка
            response.status_code = 200
            return ShiftOut(**_out(existing))
        raise AppError(409, "SHIFT_ID_CONFLICT", "Осмотр смены с таким кодом уже существует")

    if db.get(Section, str(body.section_id)) is None:
        raise validation("sectionId", "Участок не найден")
    member_ids = list(dict.fromkeys(str(m) for m in body.member_ids))
    if member_ids:
        found = set(db.scalars(select(User.id).where(User.id.in_(member_ids), User.is_active.is_(True))))
        missing = [m for m in member_ids if m not in found]
        if missing:
            raise validation("memberIds", f"Пользователь не найден: {missing[0]}")

    s = Shift(id=sid, section_id=str(body.section_id), foreman_id=user.id,
              inspected_at=body.inspected_at or utcnow(),
              members=[ShiftMember(shift_id=sid, user_id=m) for m in member_ids])
    db.add(s)
    db.commit()
    response.headers["Location"] = f"/api/v1/shifts/{sid}"
    return ShiftOut(**_out(s))


@router.get("/{shift_id}", response_model=ShiftSummary)
def get_shift(shift_id: UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = db.scalars(select(Shift).where(Shift.id == str(shift_id), visible_shifts(user))).one_or_none()
    if s is None:
        raise not_found("Осмотр смены не найден")
    base = (Defect.shift_id == s.id, visible_defects(user))
    by_cat = db.execute(select(Defect.category_id, func.count()).where(*base).group_by(Defect.category_id)).all()
    by_sev = db.execute(select(Defect.severity, func.count()).where(*base).group_by(Defect.severity)).all()
    return ShiftSummary(
        **_out(s),
        total_defects=sum(c for _, c in by_cat),
        by_category={str(k): c for k, c in by_cat},
        by_severity={k: c for k, c in by_sev},
    )
