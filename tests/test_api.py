import time
import uuid

import jwt

from app.config import settings
from tests.conftest import API, auth, login

SEC1 = "a1b2c3d4-0000-4000-8000-000000000001"
TRACK_A1 = "a1b2c3d4-0000-4000-8000-0000000000a1"
SHIFT1 = "c0a80101-0000-4000-8000-0000000000b1"
DOC_DEFECT = "7b1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def new_defect(**kw):
    body = dict(id=str(uuid.uuid4()), sectionId=SEC1, trackId=TRACK_A1, categoryId=2, typeId=14, picket=1250,
                severity="CRITICAL", comment="Тест", shiftId=SHIFT1)
    body.update(kw)
    return body


def ids(r):
    return [d["id"] for d in r.json()["items"]]


#  авторизация 
def test_login_ok_and_shape(client):
    r = client.post(f"{API}/auth/login", json={"login": "ivanov", "password": "S3cret!pass"})
    assert r.status_code == 200
    j = r.json()
    assert j["tokenType"] == "Bearer" and j["expiresIn"] == 900
    assert j["user"]["role"] == "WORKER" and j["user"]["lastName"] == "Иванов"
    assert r.headers["X-Request-Id"].startswith("req_")


def test_login_wrong_password(client):
    r = client.post(f"{API}/auth/login", json={"login": "ivanov", "password": "wrong-password"})
    assert r.status_code == 401
    e = r.json()["error"]
    assert e["code"] == "INVALID_CREDENTIALS" and e["requestId"] == r.headers["X-Request-Id"]


def test_login_validation_error_format(client):
    r = client.post(f"{API}/auth/login", json={"login": "iv", "password": "x"})
    assert r.status_code == 400
    e = r.json()["error"]
    assert e["code"] == "VALIDATION_ERROR"
    assert {d["field"] for d in e["details"]} == {"login", "password"}


def test_login_rate_limit(client):
    for _ in range(5):
        client.post(f"{API}/auth/login", json={"login": "bruteforce", "password": "wrong-password"})
    r = client.post(f"{API}/auth/login", json={"login": "bruteforce", "password": "wrong-password"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


def test_no_token_and_bad_token(client):
    r = client.get(f"{API}/defects")
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_TOKEN"
    assert "Bearer" in r.headers["WWW-Authenticate"]
    r = client.get(f"{API}/defects", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_TOKEN"


def test_expired_token(client):
    t = jwt.encode({"sub": "3f1c2a9e-5b7d-4e21-9a10-7c6d5e4f3a21", "exp": int(time.time()) - 10},
                   settings.jwt_secret, algorithm="HS256")
    r = client.get(f"{API}/defects", headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "TOKEN_EXPIRED"


def test_token_signed_with_other_key_rejected(client):
    t = jwt.encode({"sub": "3f1c2a9e-5b7d-4e21-9a10-7c6d5e4f3a21", "exp": int(time.time()) + 600},
                   "x" * 40, algorithm="HS256")
    r = client.get(f"{API}/defects", headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_TOKEN"


def test_refresh_rotation_and_reuse(client):
    t = login(client, "smirnov", "Worker#2026")
    r = client.post(f"{API}/auth/refresh", json={"refreshToken": t["refreshToken"]})
    assert r.status_code == 200
    new = r.json()
    assert new["refreshToken"] != t["refreshToken"]
    # старый refresh больше не работает
    r = client.post(f"{API}/auth/refresh", json={"refreshToken": t["refreshToken"]})
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_TOKEN"
    # новый работает
    assert client.get(f"{API}/sections", headers=auth(new)).status_code == 200


def test_logout_revokes_refresh(client):
    t = login(client, "sidorova", "Dispatch#2026")
    r = client.post(f"{API}/auth/logout", json={"refreshToken": t["refreshToken"]}, headers=auth(t))
    assert r.status_code == 204
    r = client.post(f"{API}/auth/refresh", json={"refreshToken": t["refreshToken"]})
    assert r.status_code == 401


def test_logout_other_users_session_rejected(client, ivanov):
    other = login(client, "petrov", "Foreman#2026")
    r = client.post(f"{API}/auth/logout", json={"refreshToken": other["refreshToken"]}, headers=auth(ivanov))
    assert r.status_code == 401
    assert client.post(f"{API}/auth/refresh", json={"refreshToken": other["refreshToken"]}).status_code == 200


#  справочники 
def test_reference(client, ivanov):
    s = client.get(f"{API}/sections", headers=auth(ivanov)).json()["items"]
    assert len(s) == 4
    first = next(x for x in s if x["id"] == SEC1)
    assert first["start"] == 0 and first["end"] == 2400 and len(first["tracks"]) == 2
    assert len(client.get(f"{API}/defect-categories", headers=auth(ivanov)).json()["items"]) == 6
    types = client.get(f"{API}/defect-types", headers=auth(ivanov)).json()["items"]
    assert any(t["id"] == 14 and t["categoryId"] == 2 for t in types)


#  CRUD + идемпотентность 
def test_create_idempotent_and_get(client, ivanov):
    body = new_defect()
    r = client.post(f"{API}/defects", json=body, headers=auth(ivanov))
    assert r.status_code == 201 and r.headers["Location"].endswith(body["id"])
    j = r.json()
    assert j["status"] == "NEW" and j["userId"] == ivanov["user"]["id"] and j["photoUrl"] is None
    r2 = client.post(f"{API}/defects", json=body, headers=auth(ivanov))
    assert r2.status_code == 200 and r2.json()["id"] == body["id"]
    d = client.get(f"{API}/defects/{body['id']}", headers=auth(ivanov)).json()
    assert d["history"] == []


def test_create_id_taken_by_other_user(client, ivanov, petrov):
    # DOC_DEFECT принадлежит Иванову; Петров пытается создать запись с тем же id
    r = client.post(f"{API}/defects", json=new_defect(id=DOC_DEFECT), headers=auth(petrov))
    assert r.status_code == 409 and r.json()["error"]["code"] == "DEFECT_ID_CONFLICT"


def test_create_validation(client, ivanov):
    r = client.post(f"{API}/defects", json=new_defect(picket=9999), headers=auth(ivanov))
    assert r.status_code == 422
    e = r.json()["error"]
    assert e["code"] == "PICKET_OUT_OF_RANGE" and e["details"][0]["message"] == "Допустимый диапазон 0–2400 м"

    r = client.post(f"{API}/defects", json=new_defect(picket=-5), headers=auth(ivanov))
    assert r.status_code == 400 and r.json()["error"]["details"][0]["field"] == "picket"

    r = client.post(f"{API}/defects", json=new_defect(typeId=15), headers=auth(ivanov))  # тип из другой категории
    assert r.status_code == 400 and r.json()["error"]["details"][0]["field"] == "typeId"

    bad = new_defect()
    del bad["severity"]
    r = client.post(f"{API}/defects", json=bad, headers=auth(ivanov))
    assert r.status_code == 400 and r.json()["error"]["details"][0] == {"field": "severity", "message": "Обязательное поле"}


def test_dispatcher_cannot_create(client, sidorova):
    r = client.post(f"{API}/defects", json=new_defect(shiftId=None), headers=auth(sidorova))
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"


def test_patch(client, ivanov, smirnov):
    body = new_defect()
    client.post(f"{API}/defects", json=body, headers=auth(ivanov))
    r = client.patch(f"{API}/defects/{body['id']}", json={"picket": 100, "comment": "Новый"}, headers=auth(ivanov))
    assert r.status_code == 200 and r.json()["picket"] == 100 and r.json()["comment"] == "Новый"
    assert client.patch(f"{API}/defects/{body['id']}", json={"picket": 5000}, headers=auth(ivanov)).status_code == 422
    assert client.patch(f"{API}/defects/{body['id']}", json={"picket": None}, headers=auth(ivanov)).status_code == 400


#  RLS 
def test_rls_list_isolation(client, ivanov, smirnov, sidorova):
    a = client.get(f"{API}/defects?limit=100", headers=auth(ivanov))
    b = client.get(f"{API}/defects?limit=100", headers=auth(smirnov))
    d = client.get(f"{API}/defects?limit=100", headers=auth(sidorova))
    assert d.json()["hasMore"] is False
    all_ids = set(ids(d))
    assert set(ids(a)) < all_ids and set(ids(b)) < all_ids      # исполнители видят строго подмножество
    assert DOC_DEFECT in ids(a) and DOC_DEFECT not in ids(b)   # смирнов не видит записи участка 1
    # иванов видит только свой участок и свои записи
    sections = {x["sectionId"] for x in a.json()["items"]}
    assert sections == {SEC1}


def test_rls_direct_access_returns_404_not_403(client, smirnov):
    r = client.get(f"{API}/defects/{DOC_DEFECT}", headers=auth(smirnov))
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert client.patch(f"{API}/defects/{DOC_DEFECT}", json={"comment": "x"}, headers=auth(smirnov)).status_code == 404
    r = client.post(f"{API}/defects/{DOC_DEFECT}/status", json={"toStatus": "IN_PROGRESS"}, headers=auth(smirnov))
    assert r.status_code == 404
    r = client.post(f"{API}/defects/{DOC_DEFECT}/photo", files={"file": ("p.png", PNG, "image/png")},
                    headers=auth(smirnov))
    assert r.status_code == 404
    assert client.get(f"{API}/defects/{DOC_DEFECT}/photo", headers=auth(smirnov)).status_code == 404


def test_rls_filter_cannot_bypass(client, smirnov):
    # явный фильтр по чужому участку не раскрывает чужие строки
    r = client.get(f"{API}/defects?sectionId={SEC1}", headers=auth(smirnov))
    assert r.status_code == 200 and r.json()["items"] == []


def test_rls_worker_can_see_but_not_edit_others_open_defect(client, ivanov, petrov):
    # запись бригадира без смены на участке 1: Иванов видит (открытая на его участке), но править не может
    body = new_defect(shiftId=None, comment="от бригадира")
    assert client.post(f"{API}/defects", json=body, headers=auth(petrov)).status_code == 201
    assert client.get(f"{API}/defects/{body['id']}", headers=auth(ivanov)).status_code == 200
    r = client.patch(f"{API}/defects/{body['id']}", json={"comment": "x"}, headers=auth(ivanov))
    assert r.status_code == 403
    # бригадир правит записи своей смены (запись Иванова в смене b1)
    r = client.patch(f"{API}/defects/{DOC_DEFECT}", json={"comment": "правка бригадира"}, headers=auth(petrov))
    assert r.status_code == 200


def test_rls_verified_hidden_from_section_peers_but_visible_to_author(client, ivanov, petrov):
    body = new_defect(shiftId=None, comment="verify-me")
    client.post(f"{API}/defects", json=body, headers=auth(petrov))
    for st in ("IN_PROGRESS", "FIXED", "VERIFIED"):
        r = client.post(f"{API}/defects/{body['id']}/status", json={"toStatus": st}, headers=auth(petrov))
        assert r.status_code == 200, r.text
    assert client.get(f"{API}/defects/{body['id']}", headers=auth(ivanov)).status_code == 404  # не автор, нет смены
    assert client.get(f"{API}/defects/{body['id']}", headers=auth(petrov)).status_code == 200


def test_create_with_foreign_shift_rejected(client, smirnov):
    r = client.post(f"{API}/defects", json=new_defect(), headers=auth(smirnov))
    assert r.status_code == 400 and r.json()["error"]["details"][0]["field"] == "shiftId"


#  статусы 
def test_status_flow_and_history(client, ivanov, petrov):
    body = new_defect()
    client.post(f"{API}/defects", json=body, headers=auth(ivanov))
    url = f"{API}/defects/{body['id']}/status"

    r = client.post(url, json={"toStatus": "VERIFIED"}, headers=auth(ivanov))
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"
    assert r.json()["error"]["message"] == "Нельзя перейти из NEW сразу в VERIFIED"

    hid = str(uuid.uuid4())
    r = client.post(url, json={"id": hid, "toStatus": "IN_PROGRESS"}, headers=auth(ivanov))
    assert r.status_code == 200 and r.json()["status"] == "IN_PROGRESS"
    # повтор с тем же id — без дубликата и без ошибки
    r = client.post(url, json={"id": hid, "toStatus": "IN_PROGRESS"}, headers=auth(ivanov))
    assert r.status_code == 200 and len(r.json()["history"]) == 1

    r = client.post(url, json={"toStatus": "FIXED", "comment": "Болт установлен"}, headers=auth(ivanov))
    assert r.status_code == 200 and [h["status"] for h in r.json()["history"]] == ["IN_PROGRESS", "FIXED"]

    # VERIFIED — только бригадир/диспетчер
    r = client.post(url, json={"toStatus": "VERIFIED"}, headers=auth(ivanov))
    assert r.status_code == 403
    r = client.post(url, json={"toStatus": "VERIFIED"}, headers=auth(petrov))
    assert r.status_code == 200 and r.json()["status"] == "VERIFIED"


def test_status_return_from_fixed(client, ivanov):
    body = new_defect()
    client.post(f"{API}/defects", json=body, headers=auth(ivanov))
    url = f"{API}/defects/{body['id']}/status"
    for st in ("IN_PROGRESS", "FIXED", "IN_PROGRESS"):
        assert client.post(url, json={"toStatus": st}, headers=auth(ivanov)).status_code == 200


#  список: фильтры, поиск, пагинация 
def test_filters(client, sidorova):
    r = client.get(f"{API}/defects?limit=100&status=NEW,IN_PROGRESS&severity=CRITICAL", headers=auth(sidorova))
    assert r.status_code == 200
    assert all(d["status"] in ("NEW", "IN_PROGRESS") and d["severity"] == "CRITICAL" for d in r.json()["items"])
    r = client.get(f"{API}/defects?limit=100&picketFrom=1000&picketTo=1500", headers=auth(sidorova))
    assert all(1000 <= d["picket"] <= 1500 for d in r.json()["items"])
    assert client.get(f"{API}/defects?status=BOGUS", headers=auth(sidorova)).status_code == 400
    assert client.get(f"{API}/defects?limit=500", headers=auth(sidorova)).status_code == 400
    assert client.get(f"{API}/defects?sort=bogus", headers=auth(sidorova)).status_code == 400


def test_search_cyrillic_case_insensitive(client, ivanov):
    body = new_defect(comment="Течь у ПЛАТФОРМЫ станции")
    client.post(f"{API}/defects", json=body, headers=auth(ivanov))
    for q in ("платформы", "ПЛАТФОРМЫ СТАНЦИИ", "Течь у"):
        assert body["id"] in ids(client.get(f"{API}/defects", params={"q": q}, headers=auth(ivanov))), q
    assert body["id"] not in ids(client.get(f"{API}/defects", params={"q": "вокзал"}, headers=auth(ivanov)))
    # спецсимвол LIKE не работает как маска
    assert client.get(f"{API}/defects", params={"q": "%"}, headers=auth(ivanov)).json()["items"] == []


def test_pagination_no_gaps_no_duplicates(client, sidorova):
    full = ids(client.get(f"{API}/defects?limit=100", headers=auth(sidorova)))
    assert len(full) >= 40
    for sort in ("-detectedAt", "detectedAt", "picket", "-picket"):
        seen, cursor, pages = [], None, 0
        while True:
            url = f"{API}/defects?limit=7&sort={sort}" + (f"&cursor={cursor}" if cursor else "")
            j = client.get(url, headers=auth(sidorova)).json()
            seen += [d["id"] for d in j["items"]]
            pages += 1
            if not j["hasMore"]:
                assert j["nextCursor"] is None
                break
            cursor = j["nextCursor"]
            assert pages < 50
        assert len(seen) == len(set(seen)) == len(full), sort
    # курсор от другой сортировки отклоняется
    j = client.get(f"{API}/defects?limit=2&sort=picket", headers=auth(sidorova)).json()
    r = client.get(f"{API}/defects?limit=2&sort=-picket&cursor={j['nextCursor']}", headers=auth(sidorova))
    assert r.status_code == 400
    assert client.get(f"{API}/defects?cursor=@@@", headers=auth(sidorova)).status_code == 400


def test_sort_orders(client, sidorova):
    p = [d["picket"] for d in client.get(f"{API}/defects?limit=100&sort=picket", headers=auth(sidorova)).json()["items"]]
    assert p == sorted(p)
    t = [d["detectedAt"] for d in client.get(f"{API}/defects?limit=100", headers=auth(sidorova)).json()["items"]]
    assert t == sorted(t, reverse=True)


#  фото 
def test_photo_upload_replace_and_protected_download(client, ivanov, smirnov):
    body = new_defect()
    client.post(f"{API}/defects", json=body, headers=auth(ivanov))
    url = f"{API}/defects/{body['id']}/photo"

    r = client.post(url, files={"file": ("p.png", PNG, "image/png")}, headers=auth(ivanov))
    assert r.status_code == 200 and f"/defects/{body['id']}/photo" in r.json()["photoUrl"]
    got = client.get(url, headers=auth(ivanov))
    assert got.status_code == 200 and got.content == PNG and got.headers["content-type"] == "image/png"
    assert client.get(url).status_code == 401                   # без токена фото недоступно
    assert client.get(url, headers=auth(smirnov)).status_code == 404  # RLS действует и на файлы

    jpeg = b"\xff\xd8\xff\xe0" + b"1" * 50                       # повторная загрузка заменяет фото
    r = client.post(url, files={"file": ("p.jpg", jpeg, "image/jpeg")}, headers=auth(ivanov))
    assert r.status_code == 200
    got = client.get(url, headers=auth(ivanov))
    assert got.content == jpeg and got.headers["content-type"] == "image/jpeg"


def test_photo_rejections(client, ivanov):
    body = new_defect()
    client.post(f"{API}/defects", json=body, headers=auth(ivanov))
    url = f"{API}/defects/{body['id']}/photo"
    r = client.post(url, files={"file": ("a.png", b"%PDF-1.4 not an image", "image/png")}, headers=auth(ivanov))
    assert r.status_code == 415 and r.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"  # проверка по содержимому
    big = b"\xff\xd8\xff" + b"0" * (5 * 1024 * 1024)
    r = client.post(url, files={"file": ("a.jpg", big, "image/jpeg")}, headers=auth(ivanov))
    assert r.status_code == 413 and r.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    r = client.post(url, headers=auth(ivanov))
    assert r.status_code == 400


#  смены 
def test_shifts(client, ivanov, petrov, smirnov, sidorova):
    sid = str(uuid.uuid4())
    body = {"id": sid, "sectionId": SEC1, "memberIds": [ivanov["user"]["id"]]}
    assert client.post(f"{API}/shifts", json=body, headers=auth(ivanov)).status_code == 403
    r = client.post(f"{API}/shifts", json=body, headers=auth(petrov))
    assert r.status_code == 201 and r.json()["foremanId"] == petrov["user"]["id"]
    assert client.post(f"{API}/shifts", json=body, headers=auth(petrov)).status_code == 200  # идемпотентно
    other = login(client, "petrov", "Foreman#2026")  # тот же бригадир
    assert client.post(f"{API}/shifts", json=body, headers=auth(other)).status_code == 200

    bad = dict(body, id=str(uuid.uuid4()), memberIds=[str(uuid.uuid4())])
    assert client.post(f"{API}/shifts", json=bad, headers=auth(petrov)).status_code == 400

    # видимость смены: член бригады и диспетчер — да, чужой исполнитель — нет
    assert client.get(f"{API}/shifts/{sid}", headers=auth(ivanov)).status_code == 200
    assert client.get(f"{API}/shifts/{sid}", headers=auth(sidorova)).status_code == 200
    assert client.get(f"{API}/shifts/{sid}", headers=auth(smirnov)).status_code == 404


def test_shift_summary(client, petrov):
    r = client.get(f"{API}/shifts/{SHIFT1}", headers=auth(petrov))
    assert r.status_code == 200
    j = r.json()
    assert j["totalDefects"] == sum(j["bySeverity"].values()) == sum(j["byCategory"].values()) >= 1


#  прочее 
def test_outdated_client(client, ivanov):
    r = client.get(f"{API}/sections", headers={**auth(ivanov), "X-App-Version": "0.9.0"})
    assert r.status_code == 426 and r.json()["error"]["code"] == "CLIENT_OUTDATED"
    r = client.get(f"{API}/sections", headers={**auth(ivanov), "X-App-Version": "1.2.0"})
    assert r.status_code == 200


def test_unknown_route_and_bad_uuid(client, ivanov):
    assert client.get(f"{API}/nope", headers=auth(ivanov)).json()["error"]["code"] == "NOT_FOUND"
    r = client.get(f"{API}/defects/not-a-uuid", headers=auth(ivanov))
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"
