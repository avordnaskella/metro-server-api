import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv() -> None:
    """Минимальный разбор .env без внешних зависимостей."""
    path = Path(".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/metro.db")
    storage_dir: Path = Path(os.getenv("STORAGE_DIR", "./data/storage"))
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-only-secret-change-me-0123456789abcdef")
    jwt_alg: str = "HS256"
    access_ttl_sec: int = 15 * 60
    refresh_ttl_sec: int = 30 * 24 * 3600
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/")
    seed_demo_data: bool = os.getenv("SEED_DEMO_DATA", "true").lower() == "true"
    min_app_version: str = os.getenv("MIN_APP_VERSION", "1.0.0")
    max_photo_bytes: int = 5 * 1024 * 1024
    login_max_failures: int = 5
    login_window_sec: int = 60


settings = Settings()
