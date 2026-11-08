"""
Правила доступа на уровне строк (RLS) — единое место.

Все запросы к defects / shifts строятся через эти функции, поэтому роутеры
не содержат собственных условий видимости. Строка, которую пользователь не
видит, для него «не существует» (404), а не «запрещена» (403) — так не раскрывается
сам факт её наличия. 403 возвращается, когда строка видна, но действие над ней запрещено.

Видимость неисправности (SELECT):
  DISPATCHER        — все строки
  WORKER / FOREMAN  — автор записи
                      ИЛИ запись относится к смене, где пользователь бригадир/член бригады
                      ИЛИ запись на участке одной из его смен и ещё не проверена (VERIFIED)
                         — это «ранее выявленные неисправности, которые нужно проверить или устранить»

Изменение полей (PATCH): DISPATCHER; автор; бригадир смены, к которой привязана запись.
"""
from sqlalchemy import and_, or_, select, true
from sqlalchemy.orm import Session

from .models import Defect, Shift, ShiftMember, User

WORKER, FOREMAN, DISPATCHER = "WORKER", "FOREMAN", "DISPATCHER"


def _my_shift_ids(user: User):
    member_of = select(ShiftMember.shift_id).where(ShiftMember.user_id == user.id)
    return select(Shift.id).where(or_(Shift.foreman_id == user.id, Shift.id.in_(member_of)))


def visible_defects(user: User):
    if user.role == DISPATCHER:
        return true()
    mine = _my_shift_ids(user)
    my_sections = select(Shift.section_id).where(Shift.id.in_(mine))
    return or_(
        Defect.user_id == user.id,
        Defect.shift_id.in_(mine),
        and_(Defect.section_id.in_(my_sections), Defect.status != "VERIFIED"),
    )


def visible_shifts(user: User):
    if user.role == DISPATCHER:
        return true()
    return Shift.id.in_(_my_shift_ids(user))


def can_edit_defect(db: Session, user: User, defect: Defect) -> bool:
    if user.role == DISPATCHER or defect.user_id == user.id:
        return True
    if user.role == FOREMAN and defect.shift_id:
        shift = db.get(Shift, defect.shift_id)
        return shift is not None and shift.foreman_id == user.id
    return False


def is_my_shift(db: Session, user: User, shift_id: str) -> bool:
    return db.scalar(select(Shift.id).where(Shift.id == shift_id, visible_shifts(user))) is not None
