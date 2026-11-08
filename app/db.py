from datetime import datetime, timezone

from sqlalchemy import DateTime, create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.types import TypeDecorator

from .config import settings

_is_sqlite = settings.database_url.startswith("sqlite")

if _is_sqlite:
    # папка под файл БД
    path = settings.database_url.replace("sqlite:///", "", 1)
    if path and path != ":memory:":
        import os
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    future=True,
)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_setup(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()
        # встроенный lower() в SQLite не понимает кириллицу, а поиск по комментарию нужен без учёта регистра
        dbapi_conn.create_function("lower", 1, lambda s: s.lower() if isinstance(s, str) else s, deterministic=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """Хранит время в UTC, наружу отдаёт aware-datetime."""
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
