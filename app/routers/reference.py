from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import DefectCategory, DefectType, Section, User
from ..schemas import CategoryOut, DefectTypeOut, SectionOut
from ..security import get_current_user

router = APIRouter(tags=["Reference"])


@router.get("/sections")
def list_sections(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Section).order_by(Section.name)).all()
    return {"items": [SectionOut.model_validate(r) for r in rows]}


@router.get("/defect-categories")
def list_categories(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(DefectCategory).order_by(DefectCategory.id)).all()
    return {"items": [CategoryOut.model_validate(r) for r in rows]}


@router.get("/defect-types")
def list_types(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(DefectType).order_by(DefectType.id)).all()
    return {"items": [DefectTypeOut.model_validate(r) for r in rows]}
