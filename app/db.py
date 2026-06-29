import logging
import shutil
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .config import get_settings


logger = logging.getLogger(__name__)
settings = get_settings()

# Committed snapshot bundled in the repo (used to seed a fresh persistent volume).
_SEED_DB = Path(__file__).resolve().parent.parent / "data" / "ganiq.db"


def _seed_if_missing(sqlite_path: str) -> None:
    """First boot on a fresh Railway volume: copy the committed snapshot in so the
    dashboard isn't empty. No-op locally (target == seed) and on later boots
    (target already has data) — so accumulated data is never overwritten."""
    target = Path(sqlite_path)
    if target.exists() and target.stat().st_size > 0:
        return
    if not _SEED_DB.exists() or _SEED_DB.resolve() == target.resolve():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_SEED_DB, target)
    logger.info("Seeded fresh database from snapshot: %s -> %s", _SEED_DB, target)


def _build_engine():
    db_url = settings.db_url
    connect_args = {}

    if db_url.startswith("sqlite:///"):
        sqlite_path = db_url.replace("sqlite:///", "", 1)
        db_dir = Path(sqlite_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
        _seed_if_missing(sqlite_path)
        connect_args = {"check_same_thread": False}

    return create_engine(db_url, echo=False, future=True, pool_pre_ping=True, connect_args=connect_args)


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

