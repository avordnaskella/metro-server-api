from datetime import datetime, timezone
from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer
from pydantic.alias_generators import to_camel


def _fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


UtcDt = Annotated[datetime, PlainSerializer(_fmt, return_type=str)]


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class Role(str, Enum):
    WORKER = "WORKER"
    FOREMAN = "FOREMAN"
    DISPATCHER = "DISPATCHER"


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    CRITICAL = "CRITICAL"


class DefectStatus(str, Enum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    FIXED = "FIXED"
    VERIFIED = "VERIFIED"


# ---------- auth ----------
class LoginRequest(CamelModel):
    login: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class RefreshRequest(CamelModel):
    refresh_token: str = Field(min_length=1, max_length=200)


class UserOut(CamelModel):
    id: str
    login: str
    last_name: str
    first_name: str
    middle_name: str | None = None
    role: Role


class TokenPair(CamelModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int
    user: UserOut | None = None


# ---------- справочники ----------
class TrackOut(CamelModel):
    id: str
    name: str


class SectionOut(CamelModel):
    id: str
    name: str
    start: int = Field(validation_alias="start_m")
    end: int = Field(validation_alias="end_m")
    tracks: list[TrackOut]


class CategoryOut(CamelModel):
    id: int
    name: str


class DefectTypeOut(CamelModel):
    id: int
    name: str
    category_id: int  # дополнительное поле (обратно совместимо): тип зависит от категории


# ---------- неисправности ----------
class DefectCreate(CamelModel):
    id: UUID
    section_id: UUID
    track_id: UUID
    category_id: int
    type_id: int
    picket: int = Field(ge=0)
    severity: Severity
    comment: str = Field(default="", max_length=2000)
    shift_id: UUID | None = None
    detected_at: datetime | None = None


class DefectUpdate(CamelModel):
    # None по умолчанию = «поле не передано»; явный null отклоняется валидацией типа
    track_id: UUID = Field(default=None)
    type_id: int = Field(default=None)
    picket: int = Field(default=None, ge=0)
    severity: Severity = Field(default=None)
    comment: str = Field(default=None, max_length=2000)


class StatusChangeRequest(CamelModel):
    id: UUID | None = None
    to_status: DefectStatus
    comment: str = Field(default="", max_length=2000)


class StatusChangeOut(CamelModel):
    id: str
    user_id: str
    status: DefectStatus
    comment: str
    changed_at: UtcDt


class DefectOut(CamelModel):
    id: str
    user_id: str
    shift_id: str | None
    section_id: str
    track_id: str
    category_id: int
    type_id: int
    picket: int
    severity: Severity
    status: DefectStatus
    comment: str
    detected_at: UtcDt
    photo_url: str | None = None


class DefectDetails(DefectOut):
    history: list[StatusChangeOut]


class DefectPage(CamelModel):
    items: list[DefectOut]
    next_cursor: str | None = None
    has_more: bool


# ---------- смены ----------
class ShiftCreate(CamelModel):
    id: UUID
    section_id: UUID
    member_ids: list[UUID] = Field(max_length=50)
    inspected_at: datetime | None = None


class ShiftOut(CamelModel):
    id: str
    section_id: str
    foreman_id: str
    member_ids: list[str]
    inspected_at: UtcDt


class ShiftSummary(ShiftOut):
    total_defects: int
    by_category: dict[str, int]
    by_severity: dict[str, int]
