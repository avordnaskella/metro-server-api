from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, UTCDateTime, utcnow

# Таблицы повторяют ER-диаграмму из документации:
# Пользователь, Сессия, Участок, Путь, Категория объекта, Тип неисправности,
# Неисправность, Осмотр смены, Состав бригады, История статуса.


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    login: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    last_name: Mapped[str] = mapped_column(String(100))
    first_name: Mapped[str] = mapped_column(String(100))
    middle_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    role: Mapped[str] = mapped_column(String(16))  # WORKER | FOREMAN | DISPATCHER
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class AuthSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # sha256 от refresh-токена
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Section(Base):
    __tablename__ = "sections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    start_m: Mapped[int] = mapped_column(Integer)
    end_m: Mapped[int] = mapped_column(Integer)
    tracks: Mapped[list["Track"]] = relationship(order_by="Track.name", lazy="selectin")


class Track(Base):
    __tablename__ = "tracks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    section_id: Mapped[str] = mapped_column(ForeignKey("sections.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))


class DefectCategory(Base):
    __tablename__ = "defect_categories"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))


class DefectType(Base):
    __tablename__ = "defect_types"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("defect_categories.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))


class Shift(Base):
    __tablename__ = "shifts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    section_id: Mapped[str] = mapped_column(ForeignKey("sections.id"), index=True)
    foreman_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    inspected_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    members: Mapped[list["ShiftMember"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class ShiftMember(Base):
    __tablename__ = "shift_members"
    shift_id: Mapped[str] = mapped_column(ForeignKey("shifts.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True, index=True)


class Defect(Base):
    __tablename__ = "defects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # генерирует клиент
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)  # автор
    shift_id: Mapped[str | None] = mapped_column(ForeignKey("shifts.id"), nullable=True, index=True)
    section_id: Mapped[str] = mapped_column(ForeignKey("sections.id"))
    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.id"))
    category_id: Mapped[int] = mapped_column(ForeignKey("defect_categories.id"))
    type_id: Mapped[int] = mapped_column(ForeignKey("defect_types.id"))
    picket: Mapped[int] = mapped_column(Integer)
    severity: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="NEW")
    comment: Mapped[str] = mapped_column(Text, default="")
    detected_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    photo_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    photo_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    history: Mapped[list["StatusHistory"]] = relationship(
        order_by="StatusHistory.changed_at", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_defects_detected", "detected_at", "id"),
        Index("ix_defects_picket", "picket", "id"),
        Index("ix_defects_section_status", "section_id", "status"),
    )


class StatusHistory(Base):
    __tablename__ = "status_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # генерирует клиент
    defect_id: Mapped[str] = mapped_column(ForeignKey("defects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(16))  # статус, в который перешла запись
    comment: Mapped[str] = mapped_column(Text, default="")
    changed_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
