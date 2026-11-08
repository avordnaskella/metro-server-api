"""Тестовые данные (п. 7.3 ТЗ): 4 участка, 40 неисправностей, пользователи по ролям.

    python -m app.seed --reset     # пересоздать БД и заполнить заново
"""
import random
import sys
import uuid
from datetime import timedelta

from sqlalchemy.orm import Session

from .db import Base, SessionLocal, engine, utcnow
from .models import (Defect, DefectCategory, DefectType, Section, Shift, ShiftMember, StatusHistory, Track, User)
from .security import hash_password

IVANOV = "3f1c2a9e-5b7d-4e21-9a10-7c6d5e4f3a21"   # id как в примерах документации
PETROV = "3f1c2a9e-5b7d-4e21-9a10-7c6d5e4f3a22"
SIDOROVA = "3f1c2a9e-5b7d-4e21-9a10-7c6d5e4f3a23"
SMIRNOV = "3f1c2a9e-5b7d-4e21-9a10-7c6d5e4f3a24"

DEMO_USERS = [
    # id, login, пароль, фамилия, имя, отчество, роль
    (IVANOV, "ivanov", "S3cret!pass", "Иванов", "Иван", "Иванович", "WORKER"),
    (PETROV, "petrov", "Foreman#2026", "Петров", "Пётр", "Петрович", "FOREMAN"),
    (SIDOROVA, "sidorova", "Dispatch#2026", "Сидорова", "Анна", "Сергеевна", "DISPATCHER"),
    (SMIRNOV, "smirnov", "Worker#2026", "Смирнов", "Алексей", None, "WORKER"),
]

CATEGORIES = [
    (1, "Рельсовое полотно"),
    (2, "Крепления (болты, клеммы, подкладки)"),
    (3, "Земляное полотно и основание"),
    (4, "Конструкции тоннеля"),
    (5, "Стрелочные переводы"),
    (6, "Прочее"),
]

# id, категория, название, типовые комментарии
TYPES = [
    (10, 1, "Износ головки рельса", ["Боковой износ головки рельса выше нормы"]),
    (11, 1, "Выкрашивание металла", ["Выкрашивание на поверхности катания"]),
    (12, 1, "Излом/деформация рельса", ["Деформация рельса, требуется замер"]),
    (15, 1, "Трещина рельса", ["Поперечная трещина в шейке рельса"]),
    (13, 2, "Ослаблен болт крепления", ["Болт крепления ослаблен, затяжка не держит", "Ослаблены два болта подряд"]),
    (14, 2, "Отсутствует болт крепления", ["Отсутствует болт крепления подкладки"]),
    (16, 2, "Повреждена клемма", ["Клемма скрепления сломана"]),
    (17, 2, "Износ подкладки", ["Подкладка изношена, зазор больше допуска"]),
    (20, 3, "Просадка пути", ["Просадка пути около 8 мм"]),
    (21, 3, "Загрязнение лотка/балласта", ["Лоток загрязнён, нарушен водоотвод"]),
    (22, 3, "Разрушение бетонного основания", ["Скол бетона основания у рельса"]),
    (30, 4, "Течь через обделку", ["Течь по шву обделки, на путях вода"]),
    (31, 4, "Трещина в обделке", ["Продольная трещина обделки, раскрытие около 2 мм"]),
    (32, 4, "Отслоение бетона обделки", ["Отслоение защитного слоя, арматура оголена"]),
    (40, 5, "Неплотное прилегание остряка", ["Зазор у остряка стрелки больше допустимого"]),
    (41, 5, "Повреждение крестовины", ["Выкрашивание на сердечнике крестовины"]),
    (50, 6, "Посторонние предметы", ["Строительный мусор в габарите"]),
    (51, 6, "Прочее", ["Требуется осмотр специалистом"]),
]

# (суффикс id, название, начало, конец, ids путей)
SECTIONS = [
    ("001", "Перегон А — Б", 0, 2400, ("a1", "a2")),
    ("002", "Перегон Б — В", 0, 1800, ("b1", "b2")),
    ("003", "Перегон В — Г", 0, 3100, ("c1", "c2")),
    ("004", "Перегон Г — Д", 0, 2750, ("d1", "d2")),
]
SHIFT_1 = "c0a80101-0000-4000-8000-0000000000b1"
SHIFT_2 = "c0a80101-0000-4000-8000-0000000000b2"
FIRST_DEFECT = "7b1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f"   # пример из документации


def _section_id(n: str) -> str:
    return f"a1b2c3d4-0000-4000-8000-000000000{n}"


def _track_id(code: str) -> str:
    return f"a1b2c3d4-0000-4000-8000-0000000000{code}"


def seed(db: Session) -> None:
    rnd = random.Random(2026)
    now = utcnow()

    for uid, login, pwd, ln, fn, mn, role in DEMO_USERS:
        db.add(User(id=uid, login=login, password_hash=hash_password(pwd), last_name=ln, first_name=fn,
                    middle_name=mn, role=role))
    db.add_all(DefectCategory(id=i, name=n) for i, n in CATEGORIES)
    db.add_all(DefectType(id=i, category_id=c, name=n) for i, c, n, _ in TYPES)
    for suffix, name, start, end, tracks in SECTIONS:
        db.add(Section(id=_section_id(suffix), name=name, start_m=start, end_m=end))
        for k, code in enumerate(tracks, 1):
            db.add(Track(id=_track_id(code), section_id=_section_id(suffix), name=f"Главный путь {k}"))
    db.flush()

    # смены: Иванов работает на участке 1, Смирнов — на участке 2, бригадир один на обе
    s1 = Shift(id=SHIFT_1, section_id=_section_id("001"), foreman_id=PETROV, inspected_at=now - timedelta(days=1))
    s1.members = [ShiftMember(shift_id=SHIFT_1, user_id=IVANOV)]
    s2 = Shift(id=SHIFT_2, section_id=_section_id("002"), foreman_id=PETROV, inspected_at=now - timedelta(days=2))
    s2.members = [ShiftMember(shift_id=SHIFT_2, user_id=SMIRNOV)]
    db.add_all([s1, s2])
    db.flush()

    # автор и смена зависят от участка — на участках 3 и 4 смен нет (видит диспетчер и автор)
    plan = {
        "001": ([IVANOV, PETROV], SHIFT_1), "002": ([SMIRNOV, PETROV], SHIFT_2),
        "003": ([SMIRNOV], None), "004": ([SMIRNOV], None),
    }
    sec_by_suffix = {s[0]: s for s in SECTIONS}
    statuses = ["NEW", "IN_PROGRESS", "FIXED", "VERIFIED"]

    def add_defect(did, suffix, track_code, type_row, picket, severity, status, comment, detected, author, shift):
        d = Defect(id=did, user_id=author, shift_id=shift, section_id=_section_id(suffix),
                   track_id=_track_id(track_code), category_id=type_row[1], type_id=type_row[0], picket=picket,
                   severity=severity, status=status, comment=comment, detected_at=detected)
        db.add(d)
        t = detected
        for st in statuses[1:statuses.index(status) + 1]:
            t += timedelta(hours=rnd.randint(1, 3))
            who = (PETROV if rnd.random() < .7 else SIDOROVA) if st == "VERIFIED" else author
            db.add(StatusHistory(id=str(uuid.UUID(int=rnd.getrandbits(128), version=4)), defect_id=did,
                                 user_id=who, status=st, comment="", changed_at=t))

    type_by_id = {t[0]: t for t in TYPES}
    # пример из документации
    from datetime import datetime, timezone
    add_defect(FIRST_DEFECT, "001", "a1", type_by_id[14], 1250, "CRITICAL", "NEW",
               "Отсутствует болт крепления подкладки", datetime(2026, 10, 25, 1, 14, tzinfo=timezone.utc),
               IVANOV, SHIFT_1)

    for _ in range(39):
        suffix = rnd.choices(["001", "002", "003", "004"], weights=[35, 30, 20, 15])[0]
        _, _, start, end, tracks = sec_by_suffix[suffix]
        authors, shift = plan[suffix]
        author = rnd.choice(authors)
        trow = rnd.choice(TYPES)
        add_defect(
            did=str(uuid.UUID(int=rnd.getrandbits(128), version=4)), suffix=suffix,
            track_code=rnd.choice(tracks), type_row=trow, picket=rnd.randint(start, end),
            severity=rnd.choices(["LOW", "MEDIUM", "CRITICAL"], weights=[40, 40, 20])[0],
            status=rnd.choices(statuses, weights=[35, 25, 20, 20])[0], comment=rnd.choice(trow[3]),
            detected=now - timedelta(days=rnd.uniform(1, 14)), author=author,
            shift=shift if (shift and rnd.random() < .75) else None,
        )
    db.commit()


def init_db(reset: bool = False) -> None:
    if reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def seed_if_empty() -> None:
    with SessionLocal() as db:
        if db.query(User).count() == 0:
            seed(db)


if __name__ == "__main__":
    init_db(reset="--reset" in sys.argv)
    seed_if_empty()
    print("Готово: тестовые данные загружены.")
