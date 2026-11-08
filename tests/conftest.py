import os
import shutil
import tempfile

_tmp = tempfile.mkdtemp(prefix="metro-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["STORAGE_DIR"] = f"{_tmp}/storage"
os.environ["SEED_DEMO_DATA"] = "true"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

API = "/api/v1"


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c
    shutil.rmtree(_tmp, ignore_errors=True)


def login(client, name, password):
    r = client.post(f"{API}/auth/login", json={"login": name, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def auth(tokens):
    return {"Authorization": f"Bearer {tokens['accessToken']}"}


@pytest.fixture(scope="session")
def ivanov(client):
    return login(client, "ivanov", "S3cret!pass")


@pytest.fixture(scope="session")
def petrov(client):
    return login(client, "petrov", "Foreman#2026")


@pytest.fixture(scope="session")
def sidorova(client):
    return login(client, "sidorova", "Dispatch#2026")


@pytest.fixture(scope="session")
def smirnov(client):
    return login(client, "smirnov", "Worker#2026")
